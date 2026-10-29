# Third-party notices

The adapters, scripts, schemas, fixtures and documentation of this repository
are original work of OT Fabric under the MIT licence ([LICENSE](LICENSE)).
Nothing of the upstream stacks is copied into this repository: the images
fetch them at build time, pinned by version and checksum
([versions.yaml](versions.yaml)), and keep them under their own licences.
Each image carries this file and [licenses/](licenses) in
`/usr/share/doc/canopen-interop/`.

| Image | Component | Licence | Licence text | Source |
|-------|-----------|---------|--------------|--------|
| canopennode | CANopenNode v4.1, with upstream commit cf85a4c applied (gateway token buffer, see adapters/canopennode/Dockerfile) | Apache-2.0 | [Apache-2.0](licenses/Apache-2.0.txt) (also `/usr/share/doc/canopennode/`) | In the image: `/usr/share/src/canopennode/CANopenNode-v4.1.tar.gz`; https://github.com/CANopenNode/CANopenNode/tree/v4.1, https://github.com/CANopenNode/CANopenNode/commit/cf85a4c677f4 |
| canopennode | CANopenLinux (commit f1348d40, `canopend`) | Apache-2.0 | [Apache-2.0](licenses/Apache-2.0.txt) | In the image: `/usr/share/src/canopennode/CANopenLinux-f1348d4072cdabea4c3435a13c721ac29ab4cc91.tar.gz`; https://github.com/CANopenNode/CANopenLinux |
| canopennode | CANopenEditor v4.2.3 (EDSSharp) | GPL-3.0 | — | **Build tool only**, not in the image: it generates the device's OD.c/OD.h from `fixtures/eds/interop-device.eds` (shipped in `/usr/share/src/canopennode/`); https://github.com/CANopenNode/CANopenEditor/tree/v4.2.3 |
| lely | lely-core v2.3.5 (liblely-coapp, liblely-co, liblely-io2, …, Python dcf-tools) | Apache-2.0 | [Apache-2.0](licenses/Apache-2.0.txt), [NOTICE](licenses/lely-core-NOTICE.txt) | In the image: `/usr/share/src/lely/lely-core-v2.3.5.tar.gz`; https://gitlab.com/lely_industries/lely-core/-/tree/v2.3.5 |
| lely | PyYAML 6.0.3, setuptools 80.9.0 (for dcfgen) | MIT | in the image (`site-packages/*.dist-info`) | PyPI |
| lely | empy 3.3.4 (for dcfgen) | LGPL (its README names no version) | [LGPL-3.0](licenses/python-can-LGPL-3.0.txt), [GPL-3.0](licenses/python-can-GPL-3.0.txt) | Installed from PyPI as Python source (`em.py`); http://www.alcyone.com/software/empy |
| pythoncanopen | canopen 2.4.1 | MIT | [MIT](licenses/canopen-MIT.txt) | Installed from PyPI as Python source; https://github.com/christiansandberg/canopen/tree/v2.4.1 |
| pythoncanopen | python-can 4.6.1 | LGPL-3.0-only | [LGPL-3.0](licenses/python-can-LGPL-3.0.txt), [GPL-3.0](licenses/python-can-GPL-3.0.txt) | Installed from PyPI as Python source; https://github.com/hardbyte/python-can/tree/v4.6.1 |
| pythoncanopen | packaging, typing_extensions, wrapt (python-can dependencies) | Apache-2.0 OR BSD-2-Clause, PSF-2.0, BSD-2-Clause | in the image (`site-packages/*.dist-info`) | PyPI |
| all | Debian bookworm base system, CPython 3.12 (official `python` and `debian` images) | Debian package licences (`/usr/share/doc/*/copyright`), PSF-2.0 | in the image | https://hub.docker.com/_/debian, https://hub.docker.com/_/python |

The adapters start the upstream programs (`canopend`) or link the upstream
libraries (`lely-coi` links lely-core dynamically) or import them at run
time (python canopen); they are separate MIT-licensed programs using the
upstreams' public interfaces.
