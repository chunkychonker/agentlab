# Tool-use loop (Anthropic Messages API)

The core mechanic under every hand-written agent: let Claude call a function you
define, run it, feed the result back, repeat. Client tools run in *your* code;
server tools (web_search, code_execution, etc.) run on Anthropic's side and you
never see a `tool_use` block for them.

## The loop

1. **Define tools** as dicts: `name`, `description`, `input_schema` (JSON Schema
   `type: "object"` with `properties` + `required`). Description quality decides
   whether Claude picks the tool.
2. `client.messages.create(model, max_tokens, tools, messages)`.
3. While the response `stop_reason == "tool_use"`: its `content` holds one or
   more `tool_use` blocks, each with `.id`, `.name`, `.input` (already a dict).
4. Run each tool. Append the assistant turn `{"role":"assistant","content":
   response.content}`, then a user turn whose content is a list of
   `{"type":"tool_result","tool_use_id":<id>,"content":<string>}`.
5. Call `create` again with the grown `messages`. Stop when `stop_reason` is no
   longer `"tool_use"` (usually `"end_turn"`); read the final `text` block.

## Gotchas

- `tool_result.tool_use_id` **must** match the `tool_use.id` it answers.
- You must echo the assistant's `tool_use` turn back into `messages` *before* the
  `tool_result` turn — a dangling `tool_result` is rejected.
- `tool_choice` defaults to `{"type":"auto"}`. `{"type":"auto",
  "disable_parallel_tool_use": true}` = at most one tool per turn;
  `{"type":"tool","name":...}` or `{"type":"any"}` = force a tool.
- Always cap loop iterations (`max_turns`) so a misbehaving model can't spin.
- The SDK's `Tool Runner` automates this whole loop — use it in real apps; write
  the loop by hand only to learn the mechanics.
- `tools` adds a hidden system prompt (~250-500 tokens depending on model/tool
  choice), billed as input.

## Testing without a key

Inject a fake client whose `.messages.create` returns a scripted
`tool_use` message then an `end_turn` text message. Lets you verify dispatch,
`tool_use_id` matching, and the stop condition fully offline.

Source: [tool-use overview](https://platform.claude.com/docs/en/docs/build-with-claude/tool-use/overview) (2026-07-27).

Related: [[anthropic-models]], [[anthropic-python-sdk]]
