#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""入口：python app.py [serve|parse|build] ...

不带参数时启动本地服务，然后浏览器打开 http://127.0.0.1:8765
"""
import sys

from resume_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
