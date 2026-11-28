"""Destinations: where checked documents go (Excel, SmartAcc DynaMod, AI ERP;
later: back to the phone, e-mail). Each lives in its own module with a
Destination subclass; the edition decides which ones a build ships.

Keep this file free of imports from the rest of the app: config.py imports
destinations.specs while it is still loading."""
