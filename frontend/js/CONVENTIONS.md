# Frontend conventions

- Write new React components as function components with hooks. Convert existing class components opportunistically when touching them, not in a sweeping rewrite.
- Send API requests through the shared helpers in `utils.tsx`. They publish a global error alert by default and throw `ApiError` when a caller needs local recovery or field-level handling.
- Render decimal quantities through `formatQuantity` in `utils.tsx`, never by interpolating the raw value. Quantity columns are `DECIMAL(24, 9)` and the API serialises them zero-padded, so `{quantity}` renders `24.000000000`. The helper trims the padding losslessly and takes a fallback for null: `formatQuantity(inventory?.remaining_quantity, 'Unknown')`. Keep it operating on the string — parsing to a `number` reintroduces the float artifacts the decimal column exists to avoid. Money fields are not covered; they still need a currency formatter.
