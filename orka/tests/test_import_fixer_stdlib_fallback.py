from orka.core.dependency_resolver import _stdlib_fallback
def test__stdlib_fallback_known_module_os():
    """Verify that 'os' returns ('os', None) because it is a known stdlib module."""
    result = _stdlib_fallback("os")
    assert result == ("os", None)


def test__stdlib_fallback_known_module_sys():
    """Verify that 'sys' returns ('sys', None) because it is a known stdlib module."""
    result = _stdlib_fallback("sys")
    assert result == ("sys", None)


def test__stdlib_fallback_known_module_json():
    """Verify that 'json' returns ('json', None) because it is a known stdlib module."""
    result = _stdlib_fallback("json")
    assert result == ("json", None)


def test__stdlib_fallback_known_module_datetime():
    """Verify that 'datetime' returns ('datetime', None) because it is a known stdlib module."""
    result = _stdlib_fallback("datetime")
    assert result == ("datetime", None)


def test__stdlib_fallback_known_module_typing():
    """Verify that 'typing' returns ('typing', None) because it is a known stdlib module."""
    result = _stdlib_fallback("typing")
    assert result == ("typing", None)


def test__stdlib_fallback_known_module_asyncio():
    """Verify that 'asyncio' returns ('asyncio', None) because it is a known stdlib module."""
    result = _stdlib_fallback("asyncio")
    assert result == ("asyncio", None)


def test__stdlib_fallback_unknown_module():
    """Verify that an unknown module name like 'requests' returns (None, None)."""
    result = _stdlib_fallback("requests")
    assert result == (None, None)


def test__stdlib_fallback_unknown_module_custom_package():
    """Verify that a custom package name like 'mypackage' returns (None, None)."""
    result = _stdlib_fallback("mypackage")
    assert result == (None, None)


def test__stdlib_fallback_empty_string():
    """Verify that an empty string returns (None, None) since it is not a known stdlib module."""
    result = _stdlib_fallback("")
    assert result == (None, None)


def test__stdlib_fallback_case_sensitive():
    """Verify that the lookup is case-sensitive, so 'OS' returns (None, None)."""
    result = _stdlib_fallback("OS")
    assert result == (None, None)


def test__stdlib_fallback_submodule_name():
    """Verify that a submodule-like name 'os.path' returns (None, None) since it is not in the set."""
    result = _stdlib_fallback("os.path")
    assert result == (None, None)


def test__stdlib_fallback_stdlib_module_with_underscore():
    """Verify that 'configparser' returns ('configparser', None) because it is a known stdlib module."""
    result = _stdlib_fallback("configparser")
    assert result == ("configparser", None)


def test__stdlib_fallback_stdlib_module_multiprocessing():
    """Verify that 'multiprocessing' returns ('multiprocessing', None) because it is a known stdlib module."""
    result = _stdlib_fallback("multiprocessing")
    assert result == ("multiprocessing", None)


def test__stdlib_fallback_stdlib_module_concurrent():
    """Verify that 'concurrent' returns ('concurrent', None) because it is a known stdlib module."""
    result = _stdlib_fallback("concurrent")
    assert result == ("concurrent", None)
