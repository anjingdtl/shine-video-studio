import pytest


@pytest.mark.unit
def test_supported_test_tier_and_dimension_markers_are_registered(pytestconfig):
    marker_names = {entry.split(":", 1)[0] for entry in pytestconfig.getini("markers")}
    assert {
        "unit",
        "integration",
        "full",
        "windows_only",
        "media",
        "external_service",
        "gpu_required",
    } <= marker_names


def test_unannotated_test_defaults_to_l3(request):
    assert request.node.get_closest_marker("full") is not None
    assert request.node.get_closest_marker("integration") is None
    assert request.node.get_closest_marker("unit") is None
