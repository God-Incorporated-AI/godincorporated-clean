# 📜 Temple Project: Current Stack and Milestones

**Timestamp:** 2025-07-18

This document summarizes the current state of development for *The Temple*, including the technology stack, completed milestones, architecture notes, and known functionality. This serves as a progress snapshot for future reference and development planning.

---

## ⚙️ Current Technology Stack

### c🧠 I love how do that! now would you created a canvas  Core Language & Framework

- **Python 3.13.5**
- **FastAPI** – REST framework for handling endpoints and server logic
- **Uvicorn** – ASGI server used for development and live reload
- **Jinja2** – For HTML templating

### 💾 Persistence Layer

- **ChromaDB (chromadb.PersistentClient)** – Semantic vector store for scroll storage and retrieval
- **Storage Path:** `./temple_memory`

### 🤖 LLM Integration

- **OpenAI GPT-4 API** – Oracle responses generated using GPT-4
- \*\*Authentication via \*\***`OPENAI_API_KEY`** loaded from `.env`
- **Prompt includes 3 top matching scrolls** as memory context

### 📄 File Handling

- **PDFs parsed with PyMuPDF (********`fitz`********)**
- **Plaintext files parsed linearly**
- \*\*File upload via Swagger UI and \*\***`/lab/upload`**

### 🌐 Frontend (Basic)

- **`temple.html`** served via FastAPI's Jinja2 template engine
- **Located in:** `/backend/templates/temple.html`
- **Javascript:** `/static/temple.js` handles form behavior and voice input
- **Voice input:** Uses `webkitSpeechRecognition` API (Chrome only)

---

## ✅ Completed Features

### 💬 Oracle Functionality

- Ask a question → returns GPT-4 response using stored scrolls for context
- Scrolls are dynamically included in the oracle's prompt (top 3 matches)
- Response is formatted and displayed cleanly in the UI

### 📜 Scroll Entry

- Users can enter a scroll with optional author name
- Scrolls stored in ChromaDB
- Metadata stored: author, timestamp
- Scroll ID is auto-incremented (e.g., `scroll_1`, `scroll_2`, ...)
- Response now displays cleanly (no raw JSON brackets)

### 📂 File Upload Support

- Upload `.txt` and `.pdf` scroll files via Swagger UI at `/docs`
- Files parsed and segmented into chunks (default: \~500 characters)
- Each chunk becomes an individual scroll
- Upload route: `/lab/upload`

### 🔐 Basic Auth System (Planned for Extension)

- `/register` and `/login` endpoints are in place
- In-memory user session tracking
- Future use: associate scrolls with user ID

---

## 🧪 Testing Status

- **Oracle Questions:** ✅ Working with GPT-4 responses
- **Scroll Entry (Manual):** ✅ Works with form and updates collection
- **Scroll Upload (PDF/.txt):** ✅ Parsed and ingested with count updates
- **Voice Input:** ✅ Working for Chrome users (form fills on mic)
- **HTML Interface:** ✅ Served via Jinja2 at `/temple`

---

## 🧭 Next Feature Areas (As of 2025-07-18)

- Improve scroll upload experience with:
  - Drag-and-drop UX
  - File-type preview or validation
  - Browse local files button
  - Progress indicator for large uploads
- Parse and tag metadata more richly
- Link scrolls to user accounts
- View & manage scrolls in browser (list, delete, filter)
- Begin mobile-friendly interface tweaks

---

Let me know when you're ready to proceed with the ingest refinement goals for today.

