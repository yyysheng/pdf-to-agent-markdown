# Marker adapter

Marker is an optional complex-layout path. Detect the `marker_single`/`marker`
CLI before invoking it. Keep it out of core requirements because it brings
PyTorch/model infrastructure and may download weights on first use.

The adapter is intentionally conservative for partial ranges: if the command
does not return stable page markers for every requested page, discard its
candidate output and keep the canonical page-local result. Do not report a
Marker conversion as accepted merely because the process exited zero.

Upstream references:

- <https://github.com/datalab-to/marker>
- <https://github.com/datalab-to/marker/blob/master/README.md>

The upstream README currently describes Apache-2.0 code and separate terms for
model weights. Review those terms before commercial redistribution.
