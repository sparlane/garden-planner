import globals from "globals";
import pluginJs from "@eslint/js";
import tseslint from "typescript-eslint";
import eslintReact from "@eslint-react/eslint-plugin";

export default [
  {files: ["**/*.{js,mjs,cjs,ts,jsx,tsx}"]},
  {languageOptions: { globals: globals.browser }},
  pluginJs.configs.recommended,
  ...tseslint.configs.recommended,
  eslintReact.configs["recommended-typescript"],
];
