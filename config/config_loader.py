"""
Load configuration YAML with environment variable substitution and hot reload.
"""

import os
import os.path
import re
from pathlib import Path
from typing import Any, Dict, Optional, Mapping

import yaml
from loguru import logger

_env_placeholder_pattern = re.compile(r"\$\{([^}]+)\}")

def _expand_envs(text: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Replace ${VAR} placeholders with values from environ or custom env map."""
    if not isinstance(text, str):
        return text
    envmap = env or os.environ
    
    def repl(match):
        key = match.group(1)
        if key in envmap:
            return str(envmap[key])
        return match.group(0)
    
    return _env_placeholder_pattern.sub(repl, text)

def _expand(mapping: Any, env: Optional[Mapping[str, str]] = None) -> Any:
    """Recursively expand ${VAR} placeholders in strings, dicts, lists."""
    if isinstance(mapping, dict):
        return {k: _expand(v, env) for k, v in mapping.items()}
    elif isinstance(mapping, list):
        return [_expand(v, env) for v in mapping]
    elif isinstance(mapping, str):
        return _expand_envs(mapping, env)
    else:
        return mapping

def load_config(
    path: Optional[str] = None,
    defaults: Optional[Dict[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Load config YAML, substituting env vars, and merging defaults."""
    if path is None:
        # Default paths to search, respecting AUTOMATION_CONFIG env variable
        base = Path(__file__).parent
        path_candidates = [
            os.getenv("AUTOMATION_CONFIG"),
            str(base / "config.yaml"),
        ]
        for cand in path_candidates:
            if cand and os.path.isfile(cand):
                path = cand
                break
        if not path:
            raise FileNotFoundError("No config file found and AUTOMATION_CONFIG not set.")
    
    # Read YAML
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    
    # Expand env vars
    data = _expand(data, env or os.environ)
    
    # Apply defaults if any
    if defaults:
        merged = defaults.copy()
        # Simple shallow merge
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        data = merged
    
    logger.info(f"Loaded config from {path}")
    return data

def get_section(section: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convenient fetcher for top-level config section."""
    if config is None:
        config = load_config()
    return config.get(section, {})

if __name__ == "__main__":
    cfg = load_config()
    print(yaml.dump(cfg, default_flow_style=False))