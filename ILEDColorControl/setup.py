from pathlib import Path

from setuptools import find_packages, setup


BASE_DIR = Path(__file__).resolve().parent
README = (BASE_DIR / "README.md").read_text(encoding="utf-8")


setup(
    name="ILEDColorControl",
    version="0.1.0",
    description="Validated Python control path for iLEDColor BLE panels",
    long_description=README,
    long_description_content_type="text/markdown",
    author="qiouoyo-commits",
    url="https://github.com/qiouoyo-commits/iledcolorControl",
    project_urls={
        "Repository": "https://github.com/qiouoyo-commits/iledcolorControl",
        "Issues": "https://github.com/qiouoyo-commits/iledcolorControl/issues",
    },
    license="MIT",
    license_files=("LICENSE",),
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.9",
    install_requires=["bleak>=0.22"],
    extras_require={
        "gattlib": ["gattlib"],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Hardware :: Hardware Drivers",
    ],
    keywords=[
        "ble",
        "bluetooth",
        "iledcolor",
        "rcsp",
        "led-matrix",
    ],
)
