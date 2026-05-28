import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './App.css'

// StrictMode removed — double-mount destroys MapLibre canvas before it can re-init
ReactDOM.createRoot(document.getElementById('root')).render(<App />)
