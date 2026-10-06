# Sample archive batch

For a mounted-share profile, copy `invoice.txt` and `manifest.json` into its configured source root. Publish `manifest.json.ready` only after all files are complete. Then invoke the profile's collect endpoint with `{"manifest":"manifest.json"}`. Use the profile collector configuration described in the API and release notes.

For a package upload, ZIP these two files at the package root, then POST `/api/v1/beta/archive/package` with `profile_id` and multipart `file`. Each accepted item has its own receipt and retry state. Add SHA-256 values to manifest items when a producer-side integrity check is needed.
