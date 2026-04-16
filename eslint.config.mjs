import js from "@eslint/js";

export default [
  {
    ignores: ["docs/**", "node_modules/**"],
  },
  js.configs.recommended,
  {
    files: ["Navipod/assets/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        Audio: "readonly",
        Blob: "readonly",
        CustomEvent: "readonly",
        DOMParser: "readonly",
        EventSource: "readonly",
        FormData: "readonly",
        Headers: "readonly",
        MediaMetadata: "readonly",
        URL: "readonly",
        YT: "readonly",
        alert: "readonly",
        atob: "readonly",
        btoa: "readonly",
        clearInterval: "readonly",
        clearTimeout: "readonly",
        confirm: "readonly",
        console: "readonly",
        document: "readonly",
        fetch: "readonly",
        history: "readonly",
        htmx: "readonly",
        localStorage: "readonly",
        lucide: "readonly",
        location: "readonly",
        navigator: "readonly",
        setInterval: "readonly",
        setTimeout: "readonly",
        window: "readonly"
      }
    },
    rules: {
      "no-empty": "off",
      "no-unused-vars": "off"
    }
  }
];
