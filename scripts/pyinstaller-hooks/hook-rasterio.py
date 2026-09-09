from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_delvewheel_libs_directory,
    collect_dynamic_libs,
    collect_submodules,
)

datas = collect_data_files("rasterio", excludes=["tests/**"])
binaries = collect_dynamic_libs("rasterio")
datas, binaries = collect_delvewheel_libs_directory(
    "rasterio", datas=datas, binaries=binaries
)
hiddenimports = collect_submodules(
    "rasterio", filter=lambda name: ".tests" not in name and ".rio" not in name
)
