"""services/loader (docs/TEST_PLAN.md §16)."""

import zipfile

import pytest

from app.services.loader import LoaderError, _safe_members, load
from tests.builders import (
    folder,
    kml,
    kml_line,
    kml_point,
    kml_polygon,
    placemark,
    plots,
    set_flag_bits,
    shapefile_parts,
    under,
    without,
    write_zip,
    zip_bytes,
)

MAX_EXTRACTED = 10 * 1024 * 1024


@pytest.fixture
def work_dir(tmp_path):
    return tmp_path / "work"


def load_kml(tmp_path, work_dir, document: str):
    path = tmp_path / "upload.kml"
    path.write_text(document, encoding="utf-8")
    return load(path, "KML", work_dir, MAX_EXTRACTED)


def load_zip(tmp_path, work_dir, entries: dict[str, bytes], max_extracted=MAX_EXTRACTED):
    path = write_zip(tmp_path / "upload.zip", entries)
    return load(path, "SHAPEFILE", work_dir, max_extracted)


def layer_summary(loaded):
    return [(layer.name, len(layer.frame)) for layer in loaded.layers]


# KML


TWO_FOLDERS = kml(
    folder("Parcels", placemark("Plot 12", kml_polygon(), {"owner": "Asha", "khasra": "112"}))
    + folder("Roads", placemark("Access road", kml_line()), placemark("Gate", kml_point()))
)


def test_multi_folder_kml_returns_every_layer(tmp_path, work_dir):
    loaded = load_kml(tmp_path, work_dir, TWO_FOLDERS)
    assert layer_summary(loaded) == [("Parcels", 1), ("Roads", 2)]
    assert loaded.feature_count == 3


def test_kml_layers_are_epsg_4326(tmp_path, work_dir):
    loaded = load_kml(tmp_path, work_dir, TWO_FOLDERS)
    assert all(layer.frame.crs.to_epsg() == 4326 for layer in loaded.layers)


def test_kml_keeps_extended_data_and_drops_display_fields(tmp_path, work_dir):
    parcels, roads = load_kml(tmp_path, work_dir, TWO_FOLDERS).layers

    assert list(parcels.frame.columns) == ["Name", "owner", "khasra", "geometry"]
    assert parcels.frame.loc[0, "owner"] == "Asha"
    # description, timestamp and the rest are unused in this file, so they are dropped.
    assert list(roads.frame.columns) == ["Name", "geometry"]


def test_kml_standard_field_is_kept_when_any_feature_uses_it(tmp_path, work_dir):
    described = placemark("Plot", kml_polygon()).replace(
        "</name>", "</name><description>North plot</description>"
    )
    document = kml(folder("Parcels", described, placemark("Other", kml_polygon())))
    frame = load_kml(tmp_path, work_dir, document).layers[0].frame
    assert "description" in frame.columns


def test_nested_kml_folders_become_separate_layers(tmp_path, work_dir):
    document = kml(
        "<Folder><name>Site</name>"
        + placemark("Top", kml_point())
        + folder("Inner", placemark("Deep", kml_point()))
        + "</Folder>"
    )
    assert layer_summary(load_kml(tmp_path, work_dir, document)) == [("Site", 1), ("Inner", 1)]


def test_kml_with_no_placemarks_has_no_layers(tmp_path, work_dir):
    loaded = load_kml(tmp_path, work_dir, kml(""))
    assert loaded.layers == []
    assert loaded.feature_count == 0


def test_malformed_kml_fails_without_leaking_server_paths(tmp_path, work_dir):
    with pytest.raises(LoaderError, match="The KML file could not be read") as caught:
        load_kml(tmp_path, work_dir, "this is not xml")
    assert str(tmp_path) not in str(caught.value)
    assert tmp_path.as_posix() not in str(caught.value)


# Shapefile zip: reading


def test_shapefile_crs_is_read_from_the_prj(tmp_path, work_dir):
    loaded = load_zip(tmp_path, work_dir, shapefile_parts(plots(crs="EPSG:32643")))
    assert layer_summary(loaded) == [("parcels", 1)]
    assert loaded.layers[0].frame.crs.to_epsg() == 32643
    assert loaded.layers[0].frame.loc[0, "name"] == "Plot 0"


def test_shapefile_without_prj_has_no_crs(tmp_path, work_dir):
    loaded = load_zip(tmp_path, work_dir, without(shapefile_parts(plots()), ".prj"))
    assert loaded.layers[0].frame.crs is None


def test_two_shapefiles_keep_their_own_crs(tmp_path, work_dir):
    entries = {
        **shapefile_parts(plots(crs="EPSG:4326"), stem="geographic"),
        **shapefile_parts(plots(crs="EPSG:32643"), stem="projected"),
    }
    layers = {layer.name: layer.frame.crs.to_epsg() for layer in load_zip(tmp_path, work_dir, entries).layers}
    assert layers == {"geographic": 4326, "projected": 32643}


def test_shapefile_in_a_subfolder_is_found(tmp_path, work_dir):
    loaded = load_zip(tmp_path, work_dir, under("survey/2026", shapefile_parts(plots())))
    assert layer_summary(loaded) == [("parcels", 1)]


def test_same_named_shapefiles_in_different_folders_are_told_apart(tmp_path, work_dir):
    entries = {
        **under("north", shapefile_parts(plots(count=1))),
        **under("south", shapefile_parts(plots(count=2))),
    }
    assert sorted(layer_summary(load_zip(tmp_path, work_dir, entries))) == [
        ("north/parcels", 1),
        ("south/parcels", 2),
    ]


def test_upper_case_extensions_are_read(tmp_path, work_dir):
    entries = {name.upper(): data for name, data in shapefile_parts(plots(crs="EPSG:32643")).items()}
    loaded = load_zip(tmp_path, work_dir, entries)
    assert layer_summary(loaded) == [("PARCELS", 1)]
    assert loaded.layers[0].frame.crs.to_epsg() == 32643


def test_macos_metadata_entries_are_ignored(tmp_path, work_dir):
    parts = shapefile_parts(plots())
    junk = {f"__MACOSX/._{name}": b"\x00\x05\x16\x07 resource fork" for name in parts}
    junk["._parcels.shp"] = b"\x00\x05\x16\x07"
    junk[".DS_Store"] = b"\x00\x00\x00\x01Bud1"
    loaded = load_zip(tmp_path, work_dir, {**parts, **junk})
    assert layer_summary(loaded) == [("parcels", 1)]


# Shapefile zip: file-level failures


def test_missing_dbf_is_named(tmp_path, work_dir):
    # GDAL itself would read a shapefile without its .dbf and silently return no
    # attributes, so this check cannot be left to GDAL.
    with pytest.raises(LoaderError, match=r"^Shapefile 'parcels' is missing \.dbf$"):
        load_zip(tmp_path, work_dir, without(shapefile_parts(plots()), ".dbf"))


def test_every_missing_part_is_named(tmp_path, work_dir):
    with pytest.raises(LoaderError, match=r"missing \.shx and \.dbf$"):
        load_zip(tmp_path, work_dir, without(shapefile_parts(plots()), ".shx", ".dbf"))


def test_zip_without_a_shapefile_fails(tmp_path, work_dir):
    with pytest.raises(LoaderError, match=r"no shapefile \(\.shp\)"):
        load_zip(tmp_path, work_dir, {"readme.txt": b"hello", "doc.kml": b"<kml/>"})


def test_corrupt_zip_fails_cleanly(tmp_path, work_dir):
    path = tmp_path / "upload.zip"
    path.write_bytes(b"PK\x03\x04 these are not really zip contents" * 10)
    with pytest.raises(LoaderError, match="not a valid zip archive"):
        load(path, "SHAPEFILE", work_dir, MAX_EXTRACTED)


def test_entry_with_bad_checksum_fails_as_corrupt(tmp_path, work_dir):
    data = bytearray(zip_bytes(shapefile_parts(plots())))
    # ZIP_STORED keeps the bytes as-is, so flipping one in the middle of the archive
    # corrupts an entry's content and its CRC check fails on read.
    data[len(data) // 3] ^= 0xFF
    path = tmp_path / "upload.zip"
    path.write_bytes(bytes(data))
    with pytest.raises(LoaderError, match="corrupt"):
        load(path, "SHAPEFILE", work_dir, MAX_EXTRACTED)


def test_password_protected_zip_fails_with_a_clear_message(tmp_path, work_dir):
    path = tmp_path / "upload.zip"
    path.write_bytes(set_flag_bits(zip_bytes(shapefile_parts(plots())), 0x01))
    with pytest.raises(LoaderError, match="Password-protected zips are not supported"):
        load(path, "SHAPEFILE", work_dir, MAX_EXTRACTED)


def test_unreadable_shapefile_fails_with_its_name(tmp_path, work_dir):
    parts = shapefile_parts(plots())
    parts["parcels.shp"] = b"\x00" * 100
    with pytest.raises(LoaderError, match="Shapefile 'parcels' could not be read") as caught:
        load_zip(tmp_path, work_dir, parts)
    assert str(tmp_path) not in str(caught.value)


def test_zip_that_expands_past_the_limit_is_refused(tmp_path, work_dir):
    parts = shapefile_parts(plots())
    total = sum(len(data) for data in parts.values())
    with pytest.raises(LoaderError, match="expands to more than"):
        load_zip(tmp_path, work_dir, parts, max_extracted=total - 1)


@pytest.mark.parametrize(
    "evil_name",
    ["../evil.shp", "survey/../../evil.shp", "..\\evil.shp", "/tmp/evil.shp", "C:/evil.shp"],
)
def test_zip_slip_entries_are_rejected_and_nothing_is_written(tmp_path, work_dir, evil_name):
    entries = {**shapefile_parts(plots()), evil_name: b"payload"}
    with pytest.raises(LoaderError, match="unsafe path"):
        load_zip(tmp_path, work_dir, entries)
    assert not list(tmp_path.rglob("evil.shp"))
    assert not (tmp_path.parent / "evil.shp").exists()


def test_backslash_traversal_is_rejected_on_every_platform():
    # On Windows, zipfile turns backslashes into "/" in entry names when it writes and
    # reads, so the "..\\evil.shp" case above is already normalised before the loader
    # sees it. On Linux the backslash survives, and only the loader's own replacement
    # catches it. Setting the name after construction skips zipfile's conversion, so
    # this test exercises that replacement on every platform.
    entry = zipfile.ZipInfo("placeholder")
    entry.filename = r"..\evil.shp"

    class Archive:
        def infolist(self):
            return [entry]

    with pytest.raises(LoaderError, match="unsafe path"):
        _safe_members(Archive())


# Temporary files


def test_work_folder_is_empty_after_success(tmp_path, work_dir):
    load_zip(tmp_path, work_dir, shapefile_parts(plots()))
    assert list(work_dir.iterdir()) == []


def test_work_folder_is_empty_after_failure(tmp_path, work_dir):
    parts = shapefile_parts(plots())
    parts["parcels.shp"] = b"\x00" * 100  # fails after extraction, while reading
    with pytest.raises(LoaderError):
        load_zip(tmp_path, work_dir, parts)
    assert list(work_dir.iterdir()) == []
