import React, { useState } from 'react';
import {
  Layers, Server, Cpu, Workflow, Database, ShieldCheck,
  Zap, Cloud, Activity, Terminal, ArrowRight, CheckCircle2,
  Lock, Radio, HardDrive, FileCode, Check
} from 'lucide-react';

export const ArchitectureOverview: React.FC = () => {
  const [activeLayer, setActiveLayer] = useState<string>('all');
  const [selectedEndpoint, setSelectedEndpoint] = useState<string | null>(null);

  const layers = [
    {
      id: 'presentation',
      name: '1. Presentation Layer (Frontend)',
      tech: 'Next.js 15 App Router • React 19 • Tailwind CSS v4',
      color: 'border-sky-500 bg-sky-500/10 text-sky-400',
      description: 'Role-segmented Next.js 15 frontend in /frontend with persona-based views for Employees, Managers, and Finance.',
      components: ['Employee Workspace', 'Manager Approvals', 'Finance Disbursement', 'Audit & Architecture Console']
    },
    {
      id: 'api',
      name: '2. API Layer (FastAPI Backend)',
      tech: 'FastAPI (Python 3.10) • Uvicorn • Pydantic',
      color: 'border-blue-500 bg-blue-500/10 text-blue-400',
      description: 'RESTful API Backend in /backend running FastAPI with automatic OpenAPI docs, Pydantic validation, and CORS.',
      components: ['/api/claims', '/api/claims/:id/action', '/api/ai/ocr-extract', '/api/policy-rules', '/api/audit-logs', '/api/health']
    },
    {
      id: 'services',
      name: '3. Core Business Services',
      tech: 'Python Microservices • Domain Engine',
      color: 'border-indigo-500 bg-indigo-500/10 text-indigo-400',
      description: 'Python microservices managing state transitions, expense calculations, policy verification, and fraud screening.',
      components: ['Expense Service', 'Policy Engine', 'Fraud Anomaly Screener', 'IAM Policy Refiner']
    },
    {
      id: 'ai',
      name: '4. AI Layer',
      tech: 'Server-side Gemini 2.5 Flash • OCR Engine',
      color: 'border-purple-500 bg-purple-500/10 text-purple-400',
      description: 'Multimodal AI processing receipts, reasoning over unstructured policy text, and flagging duplicate or suspicious claims.',
      components: ['OCR Vision Parser', 'Gemini Policy Reasoning', 'Receipt Line Extractor', 'Anomaly & Fraud Screener']
    },
    {
      id: 'workflow',
      name: '5. Workflow Layer',
      tech: 'AWS Step Functions • EventBridge • SQS • Lambda',
      color: 'border-amber-500 bg-amber-500/10 text-amber-400',
      description: 'Orchestrates async express state machines, dead-letter queues, and event-driven state transitions.',
      components: ['Step Functions State Machine', 'EventBridge Bus', 'SQS DLQ', 'AWS Lambda Handlers']
    },
    {
      id: 'platform',
      name: '6. Platform Layer',
      tech: 'AWS IAM • CloudWatch • KMS • RBAC',
      color: 'border-emerald-500 bg-emerald-500/10 text-emerald-400',
      description: 'Cross-cutting governance including RBAC enforcement, zero-trust IAM refiner, audit logs, and metrics.',
      components: ['RBAC Middleware', 'Immutable Audit Store', 'AWS IAM Refiner', 'CloudWatch Metrics & Tracing']
    },
    {
      id: 'data',
      name: '7. Data Layer',
      tech: 'PostgreSQL / Firestore • Redis Cache • S3 Lake',
      color: 'border-teal-500 bg-teal-500/10 text-teal-400',
      description: 'Durable structured database for expense claims, in-memory cache for policy rules, and S3 for receipt blobs.',
      components: ['Claims Store (PostgreSQL)', 'Policy Rules Cache (Redis)', 'Receipt Blob Store (S3)', 'Audit Logs Store']
    },
    {
      id: 'infrastructure',
      name: '8. Infrastructure Layer',
      tech: 'AWS ECS Fargate • Containerized Microservices',
      color: 'border-cyan-500 bg-cyan-500/10 text-cyan-400',
      description: 'Serverless container deployment with health-checks, automated scaling, and zero-downtime rolling updates.',
      components: ['Cloud Run / ECS Container', 'Application Load Balancer', 'WAF Protection', 'GuardDuty Security']
    }
  ];

  // Authorization is enforced server-side (FastAPI) from the Cognito ID token's
  // custom:role_id attribute. Cognito Groups are not used for RBAC.
  const apiRoutes = [
    { method: 'POST', path: '/api/auth/login', role: 'Public', desc: 'Exchange email + password for Cognito tokens (USER_PASSWORD_AUTH)' },
    { method: 'POST', path: '/api/auth/respond-challenge', role: 'Public', desc: 'Complete first-login NEW_PASSWORD_REQUIRED → tokens' },
    { method: 'GET', path: '/api/auth/me', role: 'Authenticated', desc: 'Current caller identity (role from custom:role_id)' },
    { method: 'GET', path: '/api/claims', role: 'All Roles (employee: own only)', desc: 'Fetch expense claims with filters (status, employee, risk)' },
    { method: 'POST', path: '/api/claims', role: 'Employee', desc: 'Submit claim; employeeId is bound to the token identity' },
    { method: 'GET', path: '/api/claims/:id', role: 'All Roles (employee: own only)', desc: 'Fetch one claim by id or claim number' },
    { method: 'PATCH', path: '/api/claims/:id', role: 'Owner (Draft only)', desc: 'Edit an unsubmitted claim; 409 once submitted' },
    { method: 'POST', path: '/api/claims/:id/action', role: 'Manager / Finance / Admin (DISBURSE: Finance/Admin)', desc: 'Execute APPROVE, REJECT, DISBURSE, or FLAG_FRAUD (409 if illegal from the current state)' },
    { method: 'GET', path: '/api/claims/:id/history', role: 'All Roles (employee: own only)', desc: 'Durable claim lifecycle history with actor + request ids' },
    { method: 'POST', path: '/api/claims/:id/assign', role: 'Manager / Finance / Admin', desc: 'Assign or clear the reviewer for the next decision' },
    { method: 'POST', path: '/api/claims/:id/comments', role: 'All Roles (employee: own only)', desc: 'Add a comment; internal notes are reviewer-only' },
    { method: 'POST', path: '/api/receipts/upload', role: 'Employee', desc: 'Upload receipt → S3 → Amazon Textract extraction' },
    { method: 'GET', path: '/api/receipts', role: 'All Roles (employee: own only)', desc: 'List receipts; employees are scoped to their own' },
    { method: 'POST', path: '/api/ai/ocr-extract', role: 'Authenticated', desc: 'Server-side Gemini 2.5 Flash OCR receipt parsing' },
    { method: 'GET', path: '/api/policy-rules', role: 'Authenticated', desc: 'Fetch the active, effective-dated policy ruleset' },
    { method: 'PUT', path: '/api/policy-rules', role: 'Finance / Admin', desc: 'Publish a new policy ruleset version (previous versions retained)' },
    { method: 'GET', path: '/api/policy-rules/:code/versions', role: 'Finance / Admin / Auditor', desc: 'Version history of one policy rule' },
    { method: 'GET', path: '/api/audit-logs', role: 'Finance / Admin / Auditor', desc: 'Query the immutable, append-only audit trail' },
    { method: 'GET', path: '/api/audit-logs/:entityType/:entityId', role: 'Finance / Admin / Auditor', desc: 'Complete audit trail for one entity (e.g. Claim/EXP-2026-1001)' },
    { method: 'POST', path: '/api/ai/refine-iam-policy', role: 'Admin', desc: 'Analyze and refine IAM wildcards with Gemini' },
    { method: 'GET', path: '/api/aws/export-code', role: 'Admin', desc: 'Export AWS Lambda / Step Functions / IAM policy spec' },
    { method: 'POST', path: '/api/admin/users', role: 'Admin', desc: 'Create/invite user via Cognito (sets custom:role_id)' },
    { method: 'GET', path: '/api/admin/users', role: 'Admin', desc: 'List Cognito users (paginated)' },
    { method: 'POST', path: '/api/admin/users/:email/role', role: 'Admin', desc: 'Change a user role (updates custom:role_id)' }
  ];

  return (
    <div className="space-y-6 text-slate-200">
      {/* Top Banner */}
      <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="text-[10px] uppercase font-bold text-sky-400 mb-1 tracking-widest font-mono flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse"></span>
            Production Architecture Specification
          </div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Layers className="w-6 h-6 text-sky-400" />
            8-Layer Enterprise Serverless Blueprint
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Fully decoupled microservices architecture powering expense ingestion, AI policy reasoning, Step Functions orchestration, and zero-trust security.
          </p>
        </div>
        <div className="flex items-center gap-2 font-mono text-xs">
          <span className="px-3 py-1 bg-slate-950 border border-slate-800 rounded text-sky-300 font-semibold">
            Status: HEALTHY
          </span>
          <span className="px-3 py-1 bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 rounded font-semibold">
            RBAC: ENFORCED
          </span>
        </div>
      </div>

      {/* Layer Selector & Diagram Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left Column: 8-Layer Hierarchy List */}
        <div className="lg:col-span-3 space-y-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <h3 className="text-xs font-bold text-slate-400 uppercase tracking-widest font-mono">
              System Topology Layers
            </h3>
            <div className="flex items-center gap-2 text-[10px] text-slate-500 font-mono">
              <span>Click a layer to inspect components</span>
            </div>
          </div>

          <div className="space-y-2">
            {layers.map((layer, index) => {
              const isSelected = activeLayer === layer.id || activeLayer === 'all';
              return (
                <div
                  key={layer.id}
                  onClick={() => setActiveLayer(activeLayer === layer.id ? 'all' : layer.id)}
                  className={`p-4 rounded border transition-all cursor-pointer ${
                    isSelected
                      ? `${layer.color} shadow-md`
                      : 'bg-slate-950/60 border-slate-800/80 opacity-60 hover:opacity-100'
                  }`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2">
                    <div className="flex items-center space-x-2">
                      <span className="font-mono text-xs font-bold uppercase">{layer.name}</span>
                    </div>
                    <span className="font-mono text-[11px] text-slate-400 bg-slate-950 px-2 py-0.5 rounded border border-slate-800 self-start sm:self-auto">
                      {layer.tech}
                    </span>
                  </div>
                  <p className="text-xs text-slate-300 mb-3 font-sans leading-relaxed">
                    {layer.description}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {layer.components.map((comp, idx) => (
                      <span
                        key={idx}
                        className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-300"
                      >
                        • {comp}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Column: Layer Specs & System Metrics */}
        <div className="space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded p-4 space-y-3">
            <h4 className="text-xs font-bold text-slate-300 uppercase tracking-widest font-mono border-b border-slate-800 pb-2 flex items-center gap-2">
              <Zap className="w-3.5 h-3.5 text-sky-400" />
              Runtime System Metrics
            </h4>
            <div className="space-y-2 font-mono text-xs">
              <div className="flex justify-between items-center text-slate-400">
                <span>AI OCR Latency:</span>
                <span className="text-emerald-400 font-bold">~420ms</span>
              </div>
              <div className="flex justify-between items-center text-slate-400">
                <span>Step Functions Exec:</span>
                <span className="text-sky-400 font-bold">~110ms</span>
              </div>
              <div className="flex justify-between items-center text-slate-400">
                <span>RBAC Check:</span>
                <span className="text-slate-200 font-bold">&lt; 5ms</span>
              </div>
              <div className="flex justify-between items-center text-slate-400">
                <span>Audit Sync:</span>
                <span className="text-emerald-400 font-bold">Synchronous</span>
              </div>
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded p-4 space-y-3">
            <h4 className="text-xs font-bold text-slate-300 uppercase tracking-widest font-mono border-b border-slate-800 pb-2 flex items-center gap-2">
              <Lock className="w-3.5 h-3.5 text-amber-400" />
              Role Isolation Matrix
            </h4>
            <div className="space-y-2 text-[11px] font-mono">
              <div className="p-2 bg-slate-950 rounded border border-slate-800 space-y-1">
                <span className="text-sky-400 font-bold">EMPLOYEE:</span>
                <p className="text-slate-400">Can only submit claims & view own submission history.</p>
              </div>
              <div className="p-2 bg-slate-950 rounded border border-slate-800 space-y-1">
                <span className="text-amber-400 font-bold">MANAGER:</span>
                <p className="text-slate-400">Can approve/reject claims submitted by direct team.</p>
              </div>
              <div className="p-2 bg-slate-950 rounded border border-slate-800 space-y-1">
                <span className="text-emerald-400 font-bold">FINANCE:</span>
                <p className="text-slate-400">Can disburse funds, edit rules, and inspect audit trails.</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* REST API Specifications */}
      <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-sky-400" />
            <h3 className="text-xs font-bold text-white uppercase tracking-widest font-mono">
              Backend REST API Endpoints Matrix
            </h3>
          </div>
          <span className="text-[10px] text-slate-400 font-mono">
            BFF Service Router: 0.0.0.0:3000
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-slate-950 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-800">
              <tr>
                <th className="p-3">HTTP Method</th>
                <th className="p-3">Endpoint Path</th>
                <th className="p-3">Required Role</th>
                <th className="p-3 font-sans">Functional Description</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/80 bg-slate-950/40">
              {apiRoutes.map((route, idx) => (
                <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                  <td className="p-3">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                      route.method === 'GET'
                        ? 'bg-sky-500/20 text-sky-300 border border-sky-500/30'
                        : route.method === 'POST'
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                        : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                    }`}>
                      {route.method}
                    </span>
                  </td>
                  <td className="p-3 text-slate-200 font-bold">{route.path}</td>
                  <td className="p-3 text-slate-400">{route.role}</td>
                  <td className="p-3 font-sans text-slate-300">{route.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
