// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-Projects-J33PLBTLQTNY
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./cockpit.css";

const el = document.getElementById("root");
if (el) {
    createRoot(el).render(<App />);
}
