"""
Plugin Loader — Automatic discovery, loading, hot-reload, and hook dispatch.
"""

import importlib
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Registry of loaded plugins: name -> module
_loaded_plugins: Dict[str, Any] = {}

# Built-in plugin whitelist — only these plugins are allowed to be loaded
# by default. Users can extend via memall.yaml under plugins.allow list.
_BUILTIN_PLUGINS = frozenset({
    "dashboard", "exporter", "notifier", "scheduler",
})


def discover_plugins() -> List[str]:
    """Scan the plugins/ directory and return a list of discovered plugin names.

    A plugin is any .py file in this package directory, excluding __init__.py
    and modules starting with underscore. Results are filtered against the
    built-in whitelist.

    Returns:
        List of whitelisted plugin module names (without .py extension).
    """
    plugin_dir = Path(__file__).parent
    plugin_names: List[str] = []

    for entry in plugin_dir.iterdir():
        if entry.suffix == ".py" and not entry.name.startswith("_"):
            name = entry.stem
            if name in _BUILTIN_PLUGINS:
                plugin_names.append(name)

    return sorted(plugin_names)


def load_plugin(name: str) -> Optional[Any]:
    """Dynamically import a plugin module by name.

    Only plugins in the built-in whitelist or explicitly allowed via config
    can be loaded.

    Args:
        name: Plugin module name (e.g. 'journal', 'dashboard').

    Returns:
        The loaded module object, or None if import failed.
    """
    if name not in _BUILTIN_PLUGINS:
        logger.warning("Refused to load '%s': not in plugin whitelist", name)
        return None

    if name in _loaded_plugins:
        return _loaded_plugins[name]

    try:
        module = importlib.import_module(f"memall.plugins.{name}")
        _loaded_plugins[name] = module
        # Call register() if available
        if hasattr(module, "register"):
            module.register()
        return module
    except ImportError as e:
        logger.error("Failed to load '%s': %s", name, e)
        return None
    except Exception as e:
        logger.error("Error loading '%s': %s", name, e)
        return None


def reload_plugin(name: str) -> Optional[Any]:
    """Hot-reload a previously loaded plugin using importlib.reload.

    Args:
        name: Plugin module name.

    Returns:
        The reloaded module, or None if it wasn't loaded before.
    """
    if name not in _loaded_plugins:
        return load_plugin(name)

    try:
        module = _loaded_plugins[name]
        reloaded = importlib.reload(module)
        _loaded_plugins[name] = reloaded
        if hasattr(reloaded, "register"):
            reloaded.register()
        return reloaded
    except Exception as e:
        logger.warning("Failed to reload '%s': %s", name, e)
        return None


def run_plugin_hook(hook_name: str, **kwargs) -> List[Any]:
    """Traverse all loaded plugins and invoke the named hook function if present.

    Args:
        hook_name: Name of the hook function (e.g. 'on_capture', 'on_retrieve').
        **kwargs: Arguments passed to each hook function.

    Returns:
        List of return values from each plugin that implemented the hook.
    """
    results: List[Any] = []

    for name, module in _loaded_plugins.items():
        if hasattr(module, hook_name):
            try:
                hook = getattr(module, hook_name)
                result = hook(**kwargs)
                results.append({"plugin": name, "result": result})
            except Exception as e:
                logger.warning("Hook '%s' in '%s' failed: %s", hook_name, name, e)

    return results


def load_all_plugins() -> Dict[str, Any]:
    """Discover and load all plugins in the plugins directory.

    Returns:
        Dict mapping plugin name to loaded module.
    """
    names = discover_plugins()
    for name in names:
        load_plugin(name)
    return dict(_loaded_plugins)


def unload_plugin(name: str) -> bool:
    """Remove a plugin from the loaded registry.

    Args:
        name: Plugin name.

    Returns:
        True if the plugin was unloaded.
    """
    if name in _loaded_plugins:
        del _loaded_plugins[name]
        return True
    return False