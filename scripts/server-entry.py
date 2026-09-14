"""Entry point for the standalone Vela Server distribution."""
from multiprocessing import freeze_support
from vela.__main__ import main

if __name__ == '__main__':
    freeze_support()
    main()
