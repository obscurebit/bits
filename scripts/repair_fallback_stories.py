#!/usr/bin/env python3
"""Regenerate legacy placeholders, preserving dated public URLs and queue mirrors.

Configure OPENAI_API_KEY, then run with --apply. No newsletter is sent.
The ignored state file makes an interrupted repair resumable.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import re
import threading

import yaml
from openai import OpenAI
import generate_story as writer
from project_paths import is_fallback_story
from update_landing import (create_edition_snapshot, get_edition_number,
                            get_links_for_date, get_story_for_date,
                            update_bits_index, update_editions_index, update_home_html)


def frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split('---', 2)[1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--state-file', type=Path, default=Path('cache/fallback_story_repair.json'))
    args = parser.parse_args()
    state = json.loads(args.state_file.read_text()) if args.state_file.exists() else {'dates': {}}
    paths = list(Path('docs/bits/posts').glob('*.md'))
    paths += list(Path('data/edition_queue').glob('*/docs/bits/posts/*.md'))
    for path in paths:
        if not is_fallback_story(path):
            continue
        day = path.name[:10]
        entry = state['dates'].setdefault(day, {'paths': [], 'theme': frontmatter(path)['theme']})
        if str(path) not in entry['paths']:
            entry['paths'].append(str(path))
    pending = sorted(day for day, entry in state['dates'].items() if not entry.get('repaired'))
    print(f'{len(state["dates"])} affected dates; {len(pending)} remaining', flush=True)
    if not args.apply:
        for day in pending:
            print(day, state['dates'][day]['theme'])
        return
    if not writer.API_KEY:
        raise RuntimeError('OPENAI_API_KEY is required')
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    args.state_file.write_text(json.dumps(state, indent=2)+'\n')
    lock = threading.Lock()
    existing_titles = {frontmatter(p).get('title', '').casefold() for p in paths if not is_fallback_story(p)}
    existing_bodies = {hashlib.sha256(writer.extract_story_body(p.read_text()).encode()).hexdigest()
                       for p in paths if not is_fallback_story(p)}
    theme_bank = writer.load_themes()['themes']
    briefs = {str(k): v for k, v in yaml.safe_load(Path('prompts/fallback_repair_briefs.yaml').read_text()).items()}
    staging = Path('cache/repaired_stories')
    os.environ['OBSCUREBIT_OUTPUT_ROOT'] = str(staging)

    def repair(day: str) -> None:
        entry = state['dates'][day]
        target = datetime.strptime(day, '%Y-%m-%d')
        theme = next((t for t in theme_bank if t['name'] == entry['theme']), None)
        if theme is None:
            raise ValueError(f'No theme direction for {entry["theme"]}')
        style = writer.select_style_modifiers(target)
        genre = style.get('genre', 'speculative fiction')
        recent = writer.collect_recent_story_context(target)
        prompt = (
            'Write an original speculative short story of about 400 words; never exceed 650 words.\n'
            'First line: a short title (2-6 words), then a blank line and the complete story. No notes.\n'
            f'Theme: {theme["story"]}\n'
            f'Scene seed: {briefs.get(day, theme["story"])}\n'
            f'Point of view: {style["pov"]}\nVoice: {style["voice_profile"]}\n'
            f'Tone: {style["tone"]}\nGenre: {genre}\n'
            'Show a consequential choice through concrete actions and dialogue. '
            'Keep the impossible fact visible, but leave its explanation unstated. '
            'Maintain physical continuity: people, objects, places and time must stay consistent. '
            'Avoid closing moral summaries, mystery scaffolding, and poetic filler. '
            'Do not use the names Delaney, Sarah, Agnes, Marla, Arthur, or any names listed in the system prompt.\n'
            f'Do not reuse these titles: {"; ".join(recent.get("titles", []))}'
        )
        client = OpenAI(api_key=writer.API_KEY, base_url=writer.API_BASE,
                        timeout=writer.OPENAI_REQUEST_TIMEOUT, max_retries=writer.OPENAI_MAX_RETRIES)
        last_problem = ''
        content = ''
        for attempt in range(4):
            print(f'DRAFT {day} attempt {attempt + 1}', flush=True)
            if content:
                revision = writer.request_chat_completion_with_retries(
                    client, model=writer.MODEL,
                    messages=[{'role': 'system', 'content': writer.load_system_prompt()},
                              {'role': 'user', 'content': prompt},
                              {'role': 'assistant', 'content': content},
                              {'role': 'user', 'content': 'Edit this draft: '+last_problem+
                               ' Fix any continuity errors. Return only a short title, blank line, and the edited story.'}],
                    temperature=.75, top_p=.95, max_tokens=2048, label='Story repair edit')
                choice = revision.choices[0]
                if choice.finish_reason != 'stop' or not choice.message.content:
                    raise ValueError(f'{day}: incomplete edit')
                content = writer.clean_story_response(choice.message.content)
            else:
                content = writer.request_story_completion(client, writer.MODEL, writer.load_system_prompt(),
                          prompt, 1.0)
            draft_path = staging / f"{day}-draft.txt"
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(content)
            title, body = writer.parse_story_output(content)
            count = len(body.split())
            if len(title.split()) > 10 or not title or title[-1:] in '.!?':
                last_problem = 'Add a 2-6 word title on its own first line. Keep the body around 400 words.'
                continue
            if not 400 <= count <= 650:
                last_problem = f'The last draft had {count} words. {"Shorten" if count > 650 else "Expand"} it to 450 words; 400-650 is mandatory.'
                continue
            digest = hashlib.sha256(body.encode()).hexdigest()
            with lock:
                if title.casefold() in existing_titles or digest in existing_bodies:
                    last_problem = 'That title or story is already in the archive. Invent a different premise and title.'
                    continue
                existing_titles.add(title.casefold())
                existing_bodies.add(digest)
                generated = writer.save_story(title, body, entry['theme'], genre, writer.MODEL,
                                               target, style.get('voice_profile', ''))
                markdown = generated.read_text()
                for raw_path in entry['paths']:
                    # Keep the original slug to preserve existing external links.
                    Path(raw_path).write_text(markdown)
                entry.update(repaired=True, title=title, words=count, model=writer.MODEL, sha256=digest)
                manifest = Path('data/edition_queue') / day / 'manifest.json'
                if manifest.exists():
                    data = json.loads(manifest.read_text())
                    data.update(story_model=writer.MODEL, story_repaired_at=datetime.now(UTC).isoformat())
                    manifest.write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')
                args.state_file.write_text(json.dumps(state, indent=2)+'\n')
                print(f'REPAIRED {day} ({count} words): {title}', flush=True)
            return
        raise ValueError(f'{day}: failed story validation after 4 drafts: {last_problem}')

    failures = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as pool:
        futures = {pool.submit(repair, day): day for day in pending}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                failures.append(f'{futures[future]}: {exc}')
                print(f'RETRY NEEDED {failures[-1]}', flush=True)
    if failures:
        raise RuntimeError('Repair incomplete; rerun to resume: '+ '; '.join(failures))

    for day, entry in sorted(state['dates'].items()):
        published = next((Path(p) for p in entry['paths'] if p.startswith('docs/bits/posts/')), None)
        if published is None:
            continue
        target = date.fromisoformat(day)
        edition = get_edition_number(target)
        story = get_story_for_date(target)
        links, _ = get_links_for_date(target)
        create_edition_snapshot(edition, story, links, {'name': entry['theme']}, target)
        newsletter = Path('docs/substack') / f'{day}-edition-{edition:03d}.md'
        if newsletter.exists():
            md = newsletter.read_text()
            title = json.dumps(f'Obscure Bit #{edition:03d}: {entry["title"]}', ensure_ascii=False)
            md = re.sub(r'^title:.*$', lambda _: 'title: '+title, md, count=1, flags=re.M)
            body = writer.extract_story_body(published.read_text())
            md, replacements = re.subn(
                r"(## 📖 Today's Bit\n\n).*?(\n---\n\n## 🔗 Today's Obscure Links)",
                lambda m: m[1]+'### '+entry['title']+'\n\n'+body+'\n'+m[2],
                md, count=1, flags=re.S)
            if replacements != 1:
                raise ValueError(f'Could not update newsletter story: {newsletter}')
            newsletter.write_text(md)
    update_bits_index()
    update_editions_index()
    today = date.today()
    story = get_story_for_date(today)
    links, total = get_links_for_date(today)
    update_home_html(story, links, total, get_edition_number(today), {'name': story['theme']})
    print(f'COMPLETE: repaired {len(state["dates"])} dates and rebuilt archives/homepage', flush=True)


if __name__ == '__main__':
    main()
