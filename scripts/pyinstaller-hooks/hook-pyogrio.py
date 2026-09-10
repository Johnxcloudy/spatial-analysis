from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_delvewheel_libs_directory,
    collect_dynamic_libs,
    collect_submodules,
)

datas = collect_data_files("pyogrio", excludes=["tests/**"])
binaries = collect_dynamic_libs("pyogrio")
datas, binaries = collect_delvewheel_libs_directory(
    "pyogrio", datas=datas, binaries=binaries
)
hiddenimports = collect_submodules(
    "pyogrio", filter=lambda name: ".tests" not in name
)
