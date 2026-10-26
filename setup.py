from setuptools import setup

setup(
    name="cbpi4-gaerdruck",
    version="0.1.0",
    description="CraftBeerPi4: Druckgärung im FermZilla (ADS1115-Drucksensor, Spund- und CO₂-Ventil)",
    author="Timm Kück",
    url="https://github.com/timmkueck/cbpi4-gaerdruck",
    packages=["cbpi4_gaerdruck"],
    install_requires=["smbus2>=0.4"],
    python_requires=">=3.9",
)
