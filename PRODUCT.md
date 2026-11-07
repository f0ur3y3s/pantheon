# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
The operator of a homelab with several LLM machines uses the dashboard to see what is serving and control each machine.

## Product Purpose
Pantheon provides one dashboard and one OpenAI-compatible gateway for Pythia, MBP, and Zeus. The dashboard shows machine state, resource use, available configurations, operation progress, and logs.

## Operating Context
Operators start, stop, and switch model configurations, edit parameters, inspect logs, and copy client configuration for opencode or pi. A machine can be offline. Operations return immediately and progress is polled.

## Capabilities and Constraints
The dashboard is a React/Vite web app in `dashboard/`. Its API request and response shapes mirror `src/pantheon/contracts.py`. Configuration IDs may contain `/`. The local simulator runs through `python -m pantheon.dev` and includes an offline machine.

## Evidence on Hand
The repository contains the dashboard implementation, API contracts, and deployment instructions. No external brand assets or performance claims were supplied.

## Product Principles
- Make serving state and ongoing operations legible at a glance.
- Keep machine controls close to the machine they affect.
- Make connection details easy to copy without hiding their scope.
