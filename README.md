# ubunut-desktop

NIKKI Desktop Agent on Ubuntu — Universal Autonomous OS-Level Desktop AI Agent.

## Overview
NIKKI is an autonomous desktop agent built for Ubuntu OS. It features a **Universal 8-Layer Capability Architecture** (449 Capabilities across 44 capability modules), supporting direct system control, AT-SPI accessibility tree navigation, Playwright browser control, document & PDF processing, process management, terminal PTY streaming, and dynamic task composition.

## Architectural Layers (449 Capabilities)
1. **Foundation Layer** (87 Caps): `app.*`, `system.*`, `window.*`, `keyboard.*`, `mouse.*`, `screen.*`
2. **Filesystem & Process Layer** (44 Caps): `fs.*`, `archive.*`, `terminal.*`, `process.*`, `clipboard.*`, `notification.*`
3. **Browser & Network Layer** (56 Caps): `browser.*`, `research.*`, `network.*`, `wifi.*`, `bluetooth.*`
4. **Hardware Layer** (48 Caps): `audio.*`, `display.*`, `printer.*`, `storage.*`, `camera.*`
5. **Documents & Media Layer** (95 Caps): `doc.*`, `sheet.*`, `pres.*`, `pdf.*`, `image.*`, `video.*`, `media.*`
6. **Development Layer** (40 Caps): `dev.*`, `git.*`, `container.*`, `vm.*`
7. **Data & Computation Layer** (25 Caps): `calc.*`, `data.*`, `comm.*`, `email.*`, `security.*`, `automation.*`, `uno.*`, `pty.*`
8. **Runtime Layer** (54 Caps): `observe.*`, `verify.*`, `wait.*`, `recover.*`

## Installation & Setup
```bash
# Set up Python virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt # or required dependencies
```

## Running NIKKI
```bash
PYTHONPATH=. .venv/bin/python agent.py "open browser and search for news"
```
