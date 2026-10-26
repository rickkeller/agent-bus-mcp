# Agent Bus MCP

This repository is the local durable core. It is not the fabric proof. Do not deploy this revision. Do not point a live worker at it. Do not enable a timer from this repository.

A worker may claim, answer, complete, or fail. It does not create a task. An answer does not become a task. Do not add secrets, host paths, chat ids, or tokens.
