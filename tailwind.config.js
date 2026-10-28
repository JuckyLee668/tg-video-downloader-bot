/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./public/index.html", "./public/miniapp.html"],
  // Classes that are picked at runtime in JS still appear as literals in the
  // HTML source, so the scanner finds them.  Keep this safelist for classes
  // that are ever assembled from fragments.
  safelist: [
    "bg-blue-500",
    "bg-red-500",
    "bg-gray-300",
    "badge-blue",
    "badge-green",
    "badge-purple",
    "badge-red",
    "badge-gray",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
