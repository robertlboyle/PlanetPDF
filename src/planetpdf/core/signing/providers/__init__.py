"""Where signing keys come from and which roots are trusted.

Cross-platform implementations live here (PKCS#12 files, user-imported CA certs).
Platform-specific ones (e.g. a future Windows Certificate Store module) implement the
same interfaces in their own module, imported only on that platform.
"""
