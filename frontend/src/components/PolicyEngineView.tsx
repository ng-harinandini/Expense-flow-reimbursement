import React, { useState } from 'react';
import { PolicyRuleDefinition } from '../types';
import { BookOpen, ShieldCheck, Check, Edit2, AlertCircle } from 'lucide-react';

interface PolicyEngineViewProps {
  policyRules: PolicyRuleDefinition[];
  onUpdateRules: (rules: PolicyRuleDefinition[]) => void;
}

export const PolicyEngineView: React.FC<PolicyEngineViewProps> = ({
  policyRules,
  onUpdateRules
}) => {
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [tempRules, setTempRules] = useState<PolicyRuleDefinition[]>([...policyRules]);
  const [isSaved, setIsSaved] = useState(false);

  const handleSave = () => {
    onUpdateRules(tempRules);
    setEditingIndex(null);
    setIsSaved(true);
    setTimeout(() => setIsSaved(false), 3000);
  };

  return (
    <div className="space-y-6 text-slate-200">
      {/* Policy Source of Truth Banner */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-lg">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-indigo-600/20 text-indigo-400 border border-indigo-500/30 flex items-center justify-center">
              <BookOpen className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">Expense Reimbursement Policy (PDF Source of Truth)</h2>
              <p className="text-xs text-slate-400">
                Authoritative rules configured in the <code className="text-indigo-300">policy_rules</code> table driving AI & automated verification.
              </p>
            </div>
          </div>
          {isSaved && (
            <div className="px-3 py-1 bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 rounded-lg text-xs font-semibold flex items-center gap-1.5 animate-pulse">
              <Check className="w-4 h-4" /> Policy Table Updated & Audited
            </div>
          )}
        </div>

        {/* General Policy Principles Card */}
        <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div className="bg-slate-950 p-3.5 rounded-lg border border-slate-800">
            <div className="font-bold text-indigo-300 mb-1">90-Day Submission Limit</div>
            <p className="text-slate-400">Claims submitted after 90 days REQUIRE Finance Director written approval and justification.</p>
          </div>
          <div className="bg-slate-950 p-3.5 rounded-lg border border-slate-800">
            <div className="font-bold text-indigo-300 mb-1">Alcohol Reimbursement Rule</div>
            <p className="text-slate-400">Alcohol non-reimbursable except Client Entertainment (capped at 2 drinks / person).</p>
          </div>
          <div className="bg-slate-950 p-3.5 rounded-lg border border-slate-800">
            <div className="font-bold text-indigo-300 mb-1">Auditability</div>
            <p className="text-slate-400">All claims are subject to audit regardless of amount or auto-approval status.</p>
          </div>
        </div>
      </div>

      {/* Interactive Policy Rule Matrix */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-white uppercase tracking-wider">
            Category Limits & Auto-Approve Rules Matrix
          </h3>
          <button
            onClick={handleSave}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-md transition-all"
          >
            Save Policy Changes & Sync
          </button>
        </div>

        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="bg-slate-950 text-slate-400 font-semibold uppercase border-b border-slate-800">
              <tr>
                <th className="p-3">Category</th>
                <th className="p-3">Grade Tiers Applicable</th>
                <th className="p-3">Max Amount ($ USD)</th>
                <th className="p-3">Auto-Approve Limit</th>
                <th className="p-3">Receipt Required Above</th>
                <th className="p-3">Special Policy Guidelines</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 bg-slate-900/40">
              {tempRules.map((rule, idx) => (
                <tr key={idx} className="hover:bg-slate-800/40 transition-colors">
                  <td className="p-3 font-semibold text-white">{rule.category}</td>
                  <td className="p-3 text-slate-300">{rule.gradeTier}</td>
                  <td className="p-3 font-mono text-emerald-300">
                    {typeof rule.maxAmountUSD === 'number' ? `$${rule.maxAmountUSD.toFixed(2)}` : rule.maxAmountUSD}
                  </td>
                  <td className="p-3 font-mono">
                    {rule.autoApproveLimitUSD === null ? (
                      <span className="text-amber-400 font-medium">Always Manual</span>
                    ) : (
                      <span className="text-indigo-300">${rule.autoApproveLimitUSD.toFixed(2)}</span>
                    )}
                  </td>
                  <td className="p-3 font-mono text-slate-300">
                    ${rule.receiptRequiredAboveUSD.toFixed(2)}
                  </td>
                  <td className="p-3 text-[11px] text-slate-400">
                    <ul className="list-disc list-inside space-y-0.5">
                      {rule.specialRules.slice(0, 2).map((sr, i) => (
                        <li key={i}>{sr}</li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
