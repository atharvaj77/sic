---
name: cdp-mcp
version: 0.3.0
description: >
  How to drive Acquia CDP MCP — campaign+audience+email is one POST+PUT,
  body params are JSON strings, _EQUALS not _OR_EQUAL.
applies_to:
  - cdp
  - acquia
  - campaignDefs
owner: data-platform
tags:
  - mcp
  - acquia
---
# CDP MCP playbook

Key rules when working with the Acquia CDP MCP server:

- Campaign + audience + email is always one POST+PUT on `/campaign/campaignDefs`.
- Tool params named `body` expect JSON strings (use `json.dumps`).
- DatasetDef comparison operators use the `_EQUALS` suffix.
- "Last N days" filters use millisecond epoch math: `NOW - N*86400000`.
