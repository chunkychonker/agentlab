# Bare-mention reference checker (demo)

`check_bare_mentions.py` checks that every file a `SKILL.md` references
exists and doesn't itself reference other `.md` files ("one-level-deep").
It detects references from markdown links and from bare filename mentions
in prose.

`skills/demo-skill/` is a normal, correctly-structured skill: `SKILL.md`
links to one leaf `REFERENCE.md`.

Run the checker:

```
python3 check_bare_mentions.py skills/demo-skill/SKILL.md
```

Run the tests:

```
python3 -m unittest test_check_bare_mentions.py -v
```
