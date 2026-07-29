This package is extracted and adapted from `solidlsp` (`src/solidlsp/`) in
[oraios/serena](https://github.com/oraios/serena), which is itself a fork of
Microsoft's [multilspy](https://github.com/microsoft/multilspy). Both are MIT
licensed.

Filesystem Supertool v2 does not track upstream; this is a fork/adopt, not a
dependency. Serena's own `serena.util` and `sensai-utils` call sites have been
inlined or reimplemented locally (see `_compat.py`) so this package has no
runtime dependency on `serena` or `sensai`.

Original copyright notice (Serena, MIT License):

```
MIT License

Copyright (c) 2025 Oraios AI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
