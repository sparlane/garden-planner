# Frontend conventions

- Write new React components as function components with hooks. Convert existing class components opportunistically when touching them, not in a sweeping rewrite.
- Send API requests through the shared helpers in `utils.tsx`. They publish a global error alert by default and throw `ApiError` when a caller needs local recovery or field-level handling.
