# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/l6e-ai/l6e-mcp/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | ------: | --------: |
| src/l6e\_mcp/\_\_init\_\_.py                 |        3 |        0 |    100% |           |
| src/l6e\_mcp/anthropic\_sync.py              |      305 |       29 |     90% |154-169, 205, 217, 225-227, 286, 310, 313, 339, 360, 468, 632-652 |
| src/l6e\_mcp/cli.py                          |       52 |        9 |     83% |73, 87-89, 106-122 |
| src/l6e\_mcp/config.py                       |      112 |       12 |     89% |39, 49-51, 76-77, 111, 127, 148-149, 187-188 |
| src/l6e\_mcp/contracts/\_\_init\_\_.py       |        0 |        0 |    100% |           |
| src/l6e\_mcp/contracts/exactness.py          |       12 |        0 |    100% |           |
| src/l6e\_mcp/contracts/mode\_coverage.py     |       20 |        1 |     95% |        14 |
| src/l6e\_mcp/core/\_\_init\_\_.py            |        0 |        0 |    100% |           |
| src/l6e\_mcp/core/authorization.py           |       68 |        3 |     96% |110, 126, 181 |
| src/l6e\_mcp/core/calibration\_cache.py      |       45 |        0 |    100% |           |
| src/l6e\_mcp/core/exactness.py               |       29 |        0 |    100% |           |
| src/l6e\_mcp/core/remote\_authorize.py       |       69 |       21 |     70% |32-38, 43-56, 113 |
| src/l6e\_mcp/core/session\_report\_worker.py |       38 |        1 |     97% |        45 |
| src/l6e\_mcp/core/status\_telemetry.py       |       53 |        1 |     98% |        60 |
| src/l6e\_mcp/outbox.py                       |      105 |        8 |     92% |88-89, 106-107, 135-137, 166 |
| src/l6e\_mcp/overhead.py                     |       18 |        1 |     94% |        31 |
| src/l6e\_mcp/server.py                       |      368 |       70 |     81% |94, 101, 104, 112, 115, 173, 188-190, 216, 437, 439, 685, 749, 797-816, 824-847, 862-914, 918-928, 932 |
| src/l6e\_mcp/session\_store.py               |       48 |        4 |     92% |100, 173, 176, 196 |
| src/l6e\_mcp/store/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/l6e\_mcp/store/\_connection.py           |       47 |        0 |    100% |           |
| src/l6e\_mcp/store/\_migrations.py           |       77 |        1 |     99% |        47 |
| src/l6e\_mcp/store/\_serialization.py        |       34 |        1 |     97% |        80 |
| src/l6e\_mcp/store/calls.py                  |      130 |        5 |     96% |171, 240, 319, 334, 372 |
| src/l6e\_mcp/store/diagnostics.py            |       19 |        0 |    100% |           |
| src/l6e\_mcp/store/schema.py                 |       17 |        0 |    100% |           |
| src/l6e\_mcp/store/sessions.py               |      108 |        3 |     97% |139, 193, 216 |
| src/l6e\_mcp/store/summary.py                |       74 |        5 |     93% |119, 122, 124, 126, 153 |
| **TOTAL**                                    | **1851** |  **175** | **91%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/l6e-ai/l6e-mcp/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/l6e-ai/l6e-mcp/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/l6e-ai/l6e-mcp/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/l6e-ai/l6e-mcp/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fl6e-ai%2Fl6e-mcp%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/l6e-ai/l6e-mcp/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.