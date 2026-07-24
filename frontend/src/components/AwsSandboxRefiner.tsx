import React, { useState } from 'react';
import { IamPolicyRefinementResponse } from '../types';
import { ShieldCheck, Sparkles, Code, Terminal, CheckCircle2, AlertOctagon, Download, Copy, Check } from 'lucide-react';

const DEFAULT_BASIC_USER_IAM_POLICY = `{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BasicServerlessAccess",
      "Effect": "Allow",
      "Action": [
        "s3:*",
        "dynamodb:*",
        "lambda:InvokeFunction",
        "states:*",
        "textract:*"
      ],
      "Resource": "*"
    }
  ]
}`;

export const AwsSandboxRefiner: React.FC = () => {
  const [userPolicyJson, setUserPolicyJson] = useState(DEFAULT_BASIC_USER_IAM_POLICY);
  const [environmentName, setEnvironmentName] = useState('ExpenseFlow AWS Serverless Sandbox');
  const [useCaseDescription, setUseCaseDescription] = useState('Step Functions + Lambda Python + Textract + S3 + DynamoDB');

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [refinementResult, setRefinementResult] = useState<IamPolicyRefinementResponse | null>(null);
  const [copiedType, setCopiedType] = useState<string | null>(null);

  const [pythonCode, setPythonCode] = useState<string>('');

  const handleRefinePolicy = async () => {
    setIsAnalyzing(true);
    try {
      const res = await fetch('/api/ai/refine-iam-policy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          currentPolicyJson: userPolicyJson,
          environmentName,
          useCaseDescription
        })
      });
      const data: IamPolicyRefinementResponse = await res.json();
      setRefinementResult(data);

      // Fetch python code
      const codeRes = await fetch('/api/aws/export-code');
      const codeData = await codeRes.json();
      setPythonCode(codeData.pythonHandler);

    } catch (err) {
      console.error('Error refining policy:', err);
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleCopy = (text: string, type: string) => {
    navigator.clipboard.writeText(text);
    setCopiedType(type);
    setTimeout(() => setCopiedType(null), 2000);
  };

  return (
    <div className="space-y-6 text-slate-200">
      {/* Header Banner */}
      <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded bg-sky-500/20 text-sky-400 border border-sky-500/40 flex items-center justify-center font-mono font-bold text-xs">
            IAM
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">AWS Sandbox & IAM Security Refiner</h2>
            <p className="text-xs text-slate-400">
              Refine your IAM policies for least-privilege security, verify locally, and export full AWS Python Serverless SAM/CDK code.
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left Column: Input Policy & Settings */}
        <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <h3 className="text-xs font-bold text-slate-300 uppercase tracking-widest font-mono">
              1. Input Basic IAM Testing Policy
            </h3>
            <span className="text-[10px] text-amber-400 font-mono bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/30">Contains Wildcards ("*")</span>
          </div>

          <textarea
            rows={12}
            value={userPolicyJson}
            onChange={(e) => setUserPolicyJson(e.target.value)}
            className="w-full bg-slate-950 font-mono text-xs text-sky-300 border border-slate-800 rounded p-3 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />

          <div className="space-y-2 text-xs font-mono">
            <div>
              <label className="block text-slate-400 mb-1 font-medium">Target Environment Name:</label>
              <input
                type="text"
                value={environmentName}
                onChange={(e) => setEnvironmentName(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200"
              />
            </div>
            <div>
              <label className="block text-slate-400 mb-1 font-medium">Serverless Architecture Context:</label>
              <input
                type="text"
                value={useCaseDescription}
                onChange={(e) => setUseCaseDescription(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200"
              />
            </div>
          </div>

          <button
            onClick={handleRefinePolicy}
            disabled={isAnalyzing}
            className="w-full py-2.5 bg-sky-500 hover:bg-sky-400 disabled:opacity-50 text-slate-950 font-bold rounded text-xs shadow-lg transition-all flex items-center justify-center gap-2 uppercase tracking-wider"
          >
            <Sparkles className="w-4 h-4 text-slate-950" />
            {isAnalyzing ? 'Analyzing Policy with AI Security Architect...' : 'Analyze & Refine IAM Policy with AI'}
          </button>
        </div>

        {/* Right Column: Refinement Results */}
        <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg space-y-4">
          <h3 className="text-xs font-bold text-slate-300 uppercase tracking-widest font-mono border-b border-slate-800 pb-2">
            2. AI Security Architect Analysis & Score
          </h3>

          {!refinementResult ? (
            <div className="bg-slate-950 p-8 rounded border border-slate-800 text-center text-slate-500 text-xs font-mono">
              Click <strong className="text-slate-300">"Analyze & Refine IAM Policy with AI"</strong> to audit wildcards, enforce least-privilege, and generate zero-trust policies.
            </div>
          ) : (
            <div className="space-y-4 text-xs">
              {/* Security Score */}
              <div className="flex items-center justify-between bg-slate-950 p-4 rounded border border-slate-800">
                <div>
                  <div className="text-slate-400 text-[11px]">Refined Security Score:</div>
                  <div className="text-2xl font-black text-emerald-400 flex items-center gap-2 font-mono">
                    {refinementResult.securityScore}/100
                    <span className="text-[10px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 px-2 py-0.5 rounded font-bold font-mono">
                      LEAST PRIVILEGE COMPLIANT
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => handleCopy(refinementResult.refinedPolicyJson, 'iam')}
                  className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs flex items-center gap-1.5 border border-slate-700 font-mono"
                >
                  {copiedType === 'iam' ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  {copiedType === 'iam' ? 'Copied' : 'Copy JSON'}
                </button>
              </div>

              {/* Fixes Made */}
              <div className="bg-slate-950 p-3.5 rounded border border-slate-800 space-y-1.5 font-mono">
                <div className="font-bold text-slate-200">Least-Privilege Violations Resolved:</div>
                <ul className="space-y-1 text-slate-300 text-[11px]">
                  {refinementResult.leastPrivilegeViolationsFixed.map((fix, idx) => (
                    <li key={idx} className="flex items-start gap-1.5">
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                      <span>{fix}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Refined JSON Policy */}
              <div className="relative">
                <span className="text-xs font-semibold text-slate-400 block mb-1 font-mono">Refined Zero-Trust IAM Policy:</span>
                <pre className="bg-slate-950 p-3 rounded border border-slate-800 text-[11px] font-mono text-emerald-300 max-h-48 overflow-y-auto">
                  {refinementResult.refinedPolicyJson}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Python Lambda Codebase Section */}
      <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-sky-400" />
            <h3 className="text-xs font-bold text-white uppercase tracking-widest font-mono">
              Python AWS Lambda Backend Codebase (Export Ready)
            </h3>
          </div>
          {pythonCode && (
            <button
              onClick={() => handleCopy(pythonCode, 'python')}
              className="px-3 py-1.5 bg-sky-500 hover:bg-sky-400 text-slate-950 rounded text-xs font-bold flex items-center gap-1.5 shadow font-mono"
            >
              {copiedType === 'python' ? <Check className="w-3.5 h-3.5 text-slate-950" /> : <Copy className="w-3.5 h-3.5" />}
              {copiedType === 'python' ? 'Copied Python' : 'Copy Python Handler'}
            </button>
          )}
        </div>

        <p className="text-xs text-slate-400">
          This Python Lambda handler implements Textract receipt OCR extraction and policy validation in AWS serverless environment.
        </p>

        {pythonCode && (
          <pre className="bg-slate-950 p-4 rounded border border-slate-800 text-xs font-mono text-sky-200 max-h-60 overflow-y-auto">
            {pythonCode}
          </pre>
        )}
      </div>
    </div>
  );
};

