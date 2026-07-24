# ExpenseFlow Orchestrator — Next.js Enterprise Expense & Fraud Platform

ExpenseFlow Orchestrator is a production-ready Next.js 15 App Router platform for enterprise expense reimbursement, automated policy enforcement, fraud detection, and AWS serverless architecture orchestration. It combines multimodal AI (Gemini 2.5 Flash), Next.js Server Route Handlers, automated policy reasoning, and Role-Based Access Control (RBAC).

---

## 🏛️ Next.js App Router & Serverless Architecture

The application is built with **Next.js 15 App Router**, styled with Tailwind CSS, and uses server-side Route Handlers for API endpoints and AI reasoning:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. NEXT.JS APP ROUTER FRONTEND                                              │
│    React 19 • Next.js 15 (`/src/app`) • Tailwind CSS • Lucide Icons         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ 2. NEXT.JS API ROUTE HANDLERS                                               │
│    `/api/claims` • `/api/policy-rules` • `/api/ai/*` • `/api/audit-logs`     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ 3. BUSINESS LOGIC & AI SERVICES                                             │
│    Policy Engine • Fraud Engine • Gemini 2.5 Flash OCR & IAM Refiner        │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ 4. SERVER-SIDE DATA & AUDIT STORE                                           │
│    In-Memory State Store / PostgreSQL • Immutable Audit Logging             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 💻 How to Run Locally After Downloading the ZIP Folder

Follow these simple steps after unzipping the downloaded archive:

### Step 1: Unzip & Open Terminal
Extract the `.zip` archive and navigate into the project root directory:
```bash
cd expenseflow-nextjs
```

### Step 2: Prerequisites
- **Node.js**: v18.18.0, v20.x, or v22.x+
- **npm**: v9.x or higher

### Step 3: Environment Variable Setup
Copy `.env.example` to create your local `.env` file:
```bash
cp .env.example .env
```

Set your optional Google Gemini API Key in `.env`:
```env
GEMINI_API_KEY=your_gemini_api_key_here
PORT=3000
NODE_ENV=development
```
*(Note: If `GEMINI_API_KEY` is not provided, the platform automatically runs using deterministic offline AI fallback parsers).*

### Step 4: Install Dependencies
```bash
npm install
```

### Step 5: Start the Next.js Development Server
```bash
npm run dev
```

Open your browser and navigate to:
**`http://localhost:3000`**

---

## 🚀 Build & Production Commands

| Command | Action |
| :--- | :--- |
| `npm run dev` | Starts Next.js App Router dev server on port 3000 |
| `npm run build` | Compiles Next.js production build (`.next`) |
| `npm run start` | Launches Next.js production server |
| `npm run lint` | Runs TypeScript typechecks (`tsc --noEmit`) |

---

## 🔌 Next.js API Routes (`src/app/api/`)

All API routes are implemented as native Next.js App Router Route Handlers:

- `GET /api/claims` — List and filter expense claims
- `POST /api/claims` — Submit new expense claim with automatic policy & fraud screening
- `POST /api/claims/[id]/action` — Execute approval, rejection, disbursement, or fraud flag
- `POST /api/ai/ocr-extract` — Parse receipt images with server-side Gemini 2.5 Flash
- `POST /api/ai/refine-iam-policy` — Zero-trust AWS IAM policy refiner
- `GET/PUT /api/policy-rules` — Read and update authoritative policy rules
- `GET /api/audit-logs` — Retrieve immutable system audit trail
- `GET /api/aws/export-code` — Download Python Lambda & Step Functions ASL definitions
