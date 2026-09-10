// Fonts are self-hosted rather than pulled from a CDN: this console runs in
// Docker on an analyst's machine and must render correctly with no egress.
// Inter was declared in the Tailwind config for months without ever being
// loaded, so the whole UI silently fell back to the system face.
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
// Tokens must be applied before any module reads them off the root element.
import './index.css'

import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
