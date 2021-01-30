import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="lirc2hass",
    version="0.1.0",
    author="Crowbar Z",
    author_email="crowbarz@outlook.com",
    description="Bridge LIRC input events to Home Assistant via REST API.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/crowbarz/lirc2hass",
    packages=["lirc2hass"],
    license="MIT",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Operating System :: POSIX :: Linux",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.6",
        "Topic :: Multimedia",
    ],
    keywords="lirc hass mce logitech harmony inputlirc home assistant rest",
    python_requires=">=3.6",
    install_requires=["requests"],
    entry_points={
        "console_scripts": [
            "lirc2hass=lirc2hass.lirc2hass:main",
        ]
    },
)
