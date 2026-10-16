# Contributing to X4 USB Bridge

Contributions are welcome, particularly compatibility testing on different Artillery printer models and firmware revisions.

## Testing and reporting

In reports, include the printer model, firmware, operating system, Python version, COM port, baud rate, and relevant error messages. **Remove passwords, Moonraker API keys, tokens, serial numbers and private network addresses** before posting.

To run the safe offline tests:

~~~shell
py -m pip install -r requirements.txt
py -m unittest discover -s tests -v
~~~

These tests do not connect to a printer. Never add automated tests that home axes, heat elements or start prints on real hardware.

## Pull requests

- Preserve localhost-only binding by default.
- Preserve the non-invasive approach: no firmware flashing, no persistent printer-side files or packages for the normal workflow.
- Add protocol regression tests for changes to binary framing or the serial relay.
- Keep changes small and explain manual hardware tests separately from simulations.
- Avoid committing G-code files, printer configuration containing secrets, logs with credentials, or any personal data.

Please do not force-push to main or disclose unpublished printer credentials. This is an independent community project.
