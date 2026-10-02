#!/usr/bin/env python3
"""
Test if the model can access the web for link curation.
"""

import os
from openai import OpenAI
from model_config import completion_options

API_BASE = os.environ.get("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1")
API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("OPENAI_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

client = OpenAI(
    api_key=API_KEY,
    base_url=API_BASE,
)

print("Testing web access capability...")
print(f"Model: {MODEL}")
print(f"API Base: {API_BASE}\n")

response = client.chat.completions.create(
    model=MODEL,
    **completion_options(MODEL),
    messages=[
        {
            "role": "system",
            "content": "You are a helpful assistant. If you can access the web, search for current information. If you cannot, clearly state that you don't have web access."
        },
        {
            "role": "user",
            "content": "Can you search the web right now and tell me 3 obscure but real Wikipedia articles that exist? Please verify they are real by accessing them."
        },
    ],
    temperature=0.3,
    max_tokens=1000,
)

content = response.choices[0].message.content.strip()

# Strip think tags if present
if "<think>" in content and "</think>" in content:
    think_end = content.find("</think>")
    content = content[think_end + len("</think>"):].strip()

print("Response:")
print("-" * 60)
print(content)
print("-" * 60)
