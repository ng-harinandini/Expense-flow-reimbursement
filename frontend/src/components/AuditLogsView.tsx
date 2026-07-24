import React from 'react';
import { AuditLogEntry } from '../types';
import { ShieldCheck, Terminal, Filter } from 'lucide-react';

interface AuditLogsViewProps {
  logs: AuditLogEntry[];
}

export const AuditLogsView: React.FC<AuditLogsViewProps> = ({ logs }) => {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded shadow-lg p-6 text-slate-200 space-y-4">
      <div className="flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-sky-400" /> Immutable System Audit Log
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Immutable trace of all claim submissions, policy validations, fraud flags, and approval decisions.
          </p>
        </div>
        <span className="text-xs font-mono text-sky-400 bg-sky-500/10 px-3 py-1 rounded border border-sky-500/30 font-semibold">
          {logs.length} Total Records Logged
        </span>
      </div>

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="w-full text-left text-xs text-slate-300">
          <thead className="bg-slate-950 text-slate-400 font-semibold uppercase border-b border-slate-800 font-mono text-[10px] tracking-wider">
            <tr>
              <th className="p-3">Timestamp</th>
              <th className="p-3">Actor / Agent</th>
              <th className="p-3">Event Type</th>
              <th className="p-3">Target Ref</th>
              <th className="p-3">Details & Notes</th>
              <th className="p-3 font-mono">IP Address</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 bg-slate-950/40 font-mono text-[11px]">
            {logs.map((log) => (
              <tr key={log.id} className="hover:bg-slate-800/40 transition-colors">
                <td className="p-3 text-slate-400 whitespace-nowrap">{log.timestamp}</td>
                <td className="p-3 text-white font-sans font-medium">{log.actor} ({log.role})</td>
                <td className="p-3">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                    log.eventType === 'FRAUD_FLAG'
                      ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                      : log.eventType === 'POLICY_EVALUATION'
                      ? 'bg-sky-500/20 text-sky-300 border border-sky-500/30'
                      : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                  }`}>
                    {log.eventType}
                  </span>
                </td>
                <td className="p-3 text-sky-300 font-bold">{log.targetId}</td>
                <td className="p-3 font-sans text-slate-300 max-w-md">{log.details}</td>
                <td className="p-3 text-slate-500">{log.ipAddress}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

