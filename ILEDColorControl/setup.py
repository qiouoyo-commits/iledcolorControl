from setuptools import setup


setup(
    name="ILEDColorControl",
    version="0.1.0",
    description="Validated Python control path for iLEDColor BLE panels",
    packages=["iledcolorcontrol"],
    python_requires=">=3.9",
    install_requires=["bleak"],
    extras_require={
        "gattlib": ["gattlib"],
    },
)
