// QR encoder for the Share sheet, on window.uqr for app.js. A module of its
// own (see maplibre-global.mjs for why it is a file) so a missing
// vendor/uqr.mjs only loses the QR code instead of keeping the map from
// starting.
import { renderSVG } from "./vendor/uqr.mjs";

window.uqr = { renderSVG };
