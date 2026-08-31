# MinerU heavy fallback

MinerU is permitted when inspection identifies scans/OCR difficulty or when
the lighter paths fail a quality gate. It is not the default for every PDF.

The upstream local CLI documented for current releases is:

```text
pip install -U "mineru[all]"
mineru -p input.pdf -o output/
mineru -p input.pdf -o output/ -b pipeline  # CPU-oriented backend
```

First use may download model files. On Windows, verify the CUDA/backend and
font environment before attempting a full textbook; WSL2/Docker is an option
for deployments that need stronger Linux compatibility. Do not hide the model
download, backend, or license constraints in a conversion report.

The upstream repository currently uses the MinerU Open Source License, based
on Apache-2.0 with additional conditions. Read the current license before
redistributing generated assets or bundling the engine.

Upstream references:

- <https://github.com/opendatalab/MinerU>
- <https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/index.md>
- <https://github.com/opendatalab/MinerU/blob/master/docs/en/faq/index.md>
