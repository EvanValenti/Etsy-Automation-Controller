"""DynamicAdapterFactory — the real, reflective AdapterFactory implementation.

Constructs a brand-new EngineAdapter instance on every call, from a
dotted "module.ClassName" binding string — the same string format
EngineRegistryService.register() already derives via
`f"{type(adapter).__module__}.{type(adapter).__qualname__}"` and persists
on the Engine row (e.g. "infra.adapters.video_generator.adapter.VideoGeneratorAdapter").

Never caches or holds an instance across calls: adapters are stateless by
design (Step 3 objective), so there is no correctness reason to reuse one,
and reconstructing per call is what removes any dependency on
process-lifetime adapter instances.

Step 4 addition: after construction, the instance is isinstance()-checked
against the (now @runtime_checkable) EngineAdapter Protocol before being
returned. This is the defensive check flagged as Step 3 finding #3 —
"worth adding before Jobs start trusting get_adapter()'s return value for
anything beyond discover()" — added now because Step 4's create_job()
calls the resolved adapter's .validate() method, the first place a wrong
or corrupted adapter_binding string would otherwise fail in a confusing,
indirect way (e.g. an AttributeError deep inside JobService) rather than
a clear, immediate one.
"""

from __future__ import annotations

import importlib

from core.adapters.adapter_factory import UnknownAdapterBindingError
from core.adapters.engine_adapter import EngineAdapter


class DynamicAdapterFactory:
    """Implements core.adapters.adapter_factory.AdapterFactory."""

    def create(self, adapter_binding: str) -> EngineAdapter:
        module_path, separator, class_name = adapter_binding.rpartition(".")
        if not separator or not module_path or not class_name:
            raise UnknownAdapterBindingError(
                adapter_binding, "not a valid 'module.ClassName' dotted path"
            )

        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise UnknownAdapterBindingError(adapter_binding, f"module import failed: {exc}") from exc

        try:
            adapter_class = getattr(module, class_name)
        except AttributeError as exc:
            raise UnknownAdapterBindingError(
                adapter_binding, f"class '{class_name}' not found in module '{module_path}'"
            ) from exc

        instance = adapter_class()

        if not isinstance(instance, EngineAdapter):
            raise UnknownAdapterBindingError(
                adapter_binding,
                f"class '{class_name}' does not implement the EngineAdapter protocol "
                "(missing one or more of discover/validate/launch/monitor/cancel/collect_results)",
            )

        return instance
