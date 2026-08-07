---
name: word-counter
description: Counts words, lines, and characters in a text file. Use when the user asks for a word count, line count, or character count of a specific file.
---

Run `scripts/count_words.py <path>` to count the words, lines, and characters
in a file. Do not open or read `count_words.py` to reproduce its algorithm
yourself — always execute it and report the JSON object it prints on stdout.

The script prints exactly one JSON object and exits nonzero on failure — for
example `{"error": "file not found: <path>"}` — instead of raising an
exception, so its output is always safe to parse and relay to the user.
