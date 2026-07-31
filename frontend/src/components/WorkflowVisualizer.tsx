import React, { useState } from 'react';
import { Layers, Play, CheckCircle2, ArrowRight, ShieldCheck, Cpu, Database, Bell } from 'lucide-react';

export const WorkflowVisualizer: React.FC = () => {
  const [activeStep, setActiveStep] = useState<string>('ocr');
  const [isSimulating, setIsSimulating] = useState(false);
  const [simulationLogs, setSimulationLogs] = useState<string[]>([
    '[00:00.01] EventBridge: ExpenseClaimCreated event emitted to bus',
    '[00:00.04] Step Functions: Task OCRTextractLambda started',
    '[00:00.08] Textract: Document text & line items extracted (Confidence 98%)',
    '[00:00.12] Step Functions: Task PolicyEngineLambda executing',
    '[00:00.15] PolicyEngine: Verified category limits & receipt requirements',
    '[00:00.18] AnomalyEngine: Checked split transactions & spend velocity',
    '[00:00.22] ChoiceState: Evaluated route -> AUTO_APPROVE',
    '[00:00.25] SQS: Event dispatched to Disbursement Queue'
  ]);

  const handleRunSim = () => {
    setIsSimulating(true);
    setSimulationLogs(['[00:00.00] Step Functions Execution Initiated (ExecutionArn: arn:aws:states:us-east-1:123456789012:execution:ExpenseApprovalWorkflow:ex-8492)']);

    const steps = [
      { id: 'ocr', name: 'OCR & Textract', log: '[00:00.05] Lambda OCR: Extracted merchant Sweetgreen #104 ($22.50)' },
      { id: 'policy', name: 'Policy Validation Engine', log: '[00:00.10] Lambda Policy: Verified within $25 auto-approve limit. Zero policy violations.' },
      { id: 'fraud', name: 'Anomaly Screening', log: '[00:00.15] Lambda Fraud: Risk score 5/100. No split transactions or duplicates found.' },
      { id: 'route', name: 'Choice Decision State', log: '[00:00.20] Choice State: Evaluated autoApproved == true -> Route to AutoApproveDisburse' },
      { id: 'disburse', name: 'Disbursement & Audit Log', log: '[00:00.25] Task Disburse: Claim status updated to auto_approved. SQS message sent.' }
    ];

    steps.forEach((s, idx) => {
      setTimeout(() => {
        setActiveStep(s.id);
        setSimulationLogs(prev => [...prev, s.log]);
        if (idx === steps.length - 1) {
          setIsSimulating(false);
        }
      }, (idx + 1) * 600);
    });
  };

  return (
    <div className="space-y-6 text-slate-200">
      {/* Header Banner */}
      <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="text-[10px] uppercase font-bold text-slate-500 mb-1 tracking-widest font-mono">
            Workflow Orchestration Layer
          </div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <Layers className="w-5 h-5 text-sky-400" /> AWS Step Functions & Serverless Workflow Visualizer
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Simulates express state machine execution, EventBridge event routing, and Lambda processing.
          </p>
        </div>

        <button
          onClick={handleRunSim}
          disabled={isSimulating}
          className="px-4 py-2 bg-sky-500 hover:bg-sky-400 disabled:opacity-50 text-slate-950 font-bold rounded text-xs flex items-center gap-2 shadow-lg transition-all self-start md:self-auto uppercase tracking-wider"
        >
          <Play className="w-3.5 h-3.5 fill-slate-950" />
          {isSimulating ? 'Executing Workflow Trace...' : 'Run Live Workflow Simulation'}
        </button>
      </div>

      {/* State Machine Flow Diagram */}
      <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-xs font-bold text-slate-400 uppercase tracking-widest font-mono">
            Step Functions State Machine Diagram
          </h3>
          <div className="flex items-center space-x-2 text-[10px] uppercase tracking-widest text-sky-400 font-mono">
            <span className="w-2 h-2 bg-sky-400 rotate-45"></span>
            <span>Saga Pattern</span>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 relative">
          {[
            { id: 'ocr', label: '1. OCR & Textract', service: 'AWS Textract + Gemini', icon: Cpu },
            { id: 'policy', label: '2. Policy Engine', service: 'Lambda Python Policy', icon: ShieldCheck },
            { id: 'fraud', label: '3. Anomaly Screening', service: 'Lambda Fraud Detector', icon: Layers },
            { id: 'route', label: '4. Choice Router', service: 'Step Functions Choice', icon: Play },
            { id: 'disburse', label: '5. Auto / Manager', service: 'SQS / DynamoDB Store', icon: Database }
          ].map((node) => {
            const IconComp = node.icon;
            const isActive = activeStep === node.id;
            return (
              <div
                key={node.id}
                onClick={() => setActiveStep(node.id)}
                className={`p-4 rounded border transition-all cursor-pointer ${
                  isActive
                    ? 'bg-sky-900/40 border-sky-500 text-sky-200 shadow-lg shadow-sky-500/10 scale-105'
                    : 'bg-slate-950 border-slate-800 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className={`w-7 h-7 rounded flex items-center justify-center ${
                    isActive ? 'bg-sky-500 text-slate-950 font-bold' : 'bg-slate-800 text-slate-400'
                  }`}>
                    <IconComp className="w-3.5 h-3.5" />
                  </div>
                  {isActive && <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />}
                </div>
                <div className="font-semibold text-xs text-white mb-0.5">{node.label}</div>
                <div className="text-[10px] text-slate-400 font-mono">{node.service}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Simulation Execution Logs */}
      <div className="bg-slate-950 border border-slate-800 rounded p-6 shadow-lg font-mono text-xs">
        <div className="flex items-center justify-between mb-3 border-b border-slate-800 pb-2">
          <span className="font-bold text-slate-300">AWS CloudWatch Live Execution Logs:</span>
          <span className="text-[10px] uppercase text-sky-400 font-mono">Express Workflow State Machine</span>
        </div>
        <div className="space-y-1 text-slate-300 max-h-48 overflow-y-auto bg-slate-900/80 p-4 rounded border border-slate-800">
          {simulationLogs.map((log, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <span className="text-emerald-400 font-semibold">❯</span>
              <span>{log}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

