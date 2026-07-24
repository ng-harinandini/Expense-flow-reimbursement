import React from 'react';
import { UserRole, Employee } from '../types';
import { ShieldCheck, User, AlertTriangle, Layers, FileCheck, DollarSign, Cpu, Layout } from 'lucide-react';

interface HeaderProps {
  currentRole: UserRole;
  currentEmployee: Employee;
  employees: Employee[];
  onRoleChange: (role: UserRole, emp: Employee) => void;
  activeTab: string;
  setActiveTab: (tab: string) => void;
  isArchInspectorOpen: boolean;
  setIsArchInspectorOpen: (open: boolean) => void;
  stats: {
    pendingCount: number;
    flaggedCount: number;
    autoApproveRate: number;
    totalDisbursedUSD: number;
  };
}

export const Header: React.FC<HeaderProps> = ({
  currentRole,
  currentEmployee,
  employees,
  onRoleChange,
  activeTab,
  setActiveTab,
  isArchInspectorOpen,
  setIsArchInspectorOpen,
  stats
}) => {

  // Role-specific primary tabs
  const getRoleTabs = () => {
    if (isArchInspectorOpen) {
      return [
        { id: 'architecture', label: '8-Layer Architecture Blueprint' },
        { id: 'workflow', label: 'AWS Step Functions Visualizer' },
        { id: 'iam', label: 'IAM Security & Policy Refiner' }
      ];
    }

    switch (currentRole) {
      case 'employee':
        return [
          { id: 'workspace', label: '+ Submit Expense' },
          { id: 'my-claims', label: 'My Expense Claims' },
          { id: 'policy-info', label: 'Expense Policy Guidelines' }
        ];
      case 'manager':
        return [
          { id: 'approvals', label: 'Pending Team Approvals' },
          { id: 'team-claims', label: 'Team Expense Directory' },
          { id: 'policy-info', label: 'Expense Policy Guidelines' }
        ];
      case 'finance':
      case 'admin':
        return [
          { id: 'disbursements', label: 'Disbursement Console' },
          { id: 'claims', label: 'Claims Directory' },
          { id: 'policy', label: 'Policy Engine Rules' },
          { id: 'audit', label: 'Immutable Audit Trail' }
        ];
      default:
        return [
          { id: 'workspace', label: 'Submit Expense' },
          { id: 'claims', label: 'Claims Console' }
        ];
    }
  };

  const activeTabsList = getRoleTabs();

  return (
    <header className="bg-[#020617] text-slate-300 border-b border-slate-800 sticky top-0 z-30 shadow-xl select-none">
      {/* Top Bar */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 bg-sky-500 rounded-sm flex items-center justify-center font-bold text-slate-900 shadow-md shadow-sky-500/20">
            S
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-lg font-semibold tracking-tight text-white flex items-center gap-2">
                ExpenseFlow Orchestrator
                <span className="text-sky-500 font-mono text-xs font-normal">v4.2.0-STABLE</span>
              </h1>
            </div>
            <p className="text-[11px] text-slate-400 uppercase tracking-wider font-mono">
              Enterprise Serverless • AI Policy & Fraud Engine
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs uppercase tracking-widest font-mono">
          {/* Architecture / System View Toggle Button */}
          <button
            onClick={() => {
              const nextState = !isArchInspectorOpen;
              setIsArchInspectorOpen(nextState);
              if (nextState) {
                setActiveTab('architecture');
              } else {
                if (currentRole === 'employee') setActiveTab('workspace');
                else if (currentRole === 'manager') setActiveTab('approvals');
                else setActiveTab('disbursements');
              }
            }}
            className={`px-3 py-1 rounded flex items-center gap-2 font-mono text-xs transition-all border ${
              isArchInspectorOpen
                ? 'bg-sky-500 text-slate-950 font-bold border-sky-400 shadow-md shadow-sky-500/20'
                : 'bg-slate-900 text-sky-400 border-sky-500/40 hover:bg-slate-800'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            {isArchInspectorOpen ? 'Exit Architecture View' : 'Architecture Inspector'}
          </button>

          {/* Persona Switcher */}
          <div className="flex items-center space-x-2 bg-slate-900 p-1 rounded border border-slate-800">
            <User className="w-3.5 h-3.5 text-sky-400 ml-1" />
            <select
              value={currentEmployee.id}
              onChange={(e) => {
                const selectedEmp = employees.find(emp => emp.id === e.target.value);
                if (selectedEmp) {
                  let role: UserRole = 'employee';
                  if (selectedEmp.grade === 'L5') role = 'manager';
                  if (selectedEmp.department === 'Finance') role = 'finance';
                  onRoleChange(role, selectedEmp);
                  setIsArchInspectorOpen(false);
                  if (role === 'employee') setActiveTab('workspace');
                  else if (role === 'manager') setActiveTab('approvals');
                  else setActiveTab('disbursements');
                }
              }}
              className="bg-slate-950 text-slate-200 text-xs rounded px-2 py-1 border border-slate-800 focus:outline-none focus:ring-1 focus:ring-sky-500 font-sans"
            >
              {employees.map(emp => (
                <option key={emp.id} value={emp.id}>
                  {emp.name} ({emp.grade} - {emp.department})
                </option>
              ))}
            </select>
            <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${
              currentRole === 'employee'
                ? 'bg-sky-900/40 text-sky-300 border-sky-500/50'
                : currentRole === 'manager'
                ? 'bg-amber-900/40 text-amber-300 border-amber-500/50'
                : 'bg-emerald-900/40 text-emerald-300 border-emerald-500/50'
            }`}>
              {currentRole.toUpperCase()}
            </span>
          </div>
        </div>
      </div>

      {/* Role Context Bar & Live Metrics */}
      <div className="bg-slate-900/60 border-t border-b border-slate-800/80">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-2 flex flex-col sm:flex-row justify-between items-center gap-2 text-xs font-mono">
          <div className="flex items-center space-x-2 text-slate-400">
            <span className="text-slate-500 uppercase text-[10px]">Active View Context:</span>
            <span className="text-white font-semibold flex items-center gap-1.5">
              {isArchInspectorOpen ? (
                <span className="text-sky-400">⚡ Platform Architecture & Cloud Topology</span>
              ) : currentRole === 'employee' ? (
                <span>👤 Employee Self-Service Interface ({currentEmployee.name})</span>
              ) : currentRole === 'manager' ? (
                <span>👔 Manager Review Portal ({currentEmployee.department} Dept)</span>
              ) : (
                <span>🏦 Finance & Audit Control Room</span>
              )}
            </span>
          </div>

          <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-2 text-slate-400">
              <FileCheck className="w-3.5 h-3.5 text-sky-400" />
              <span>Pending: <strong className="text-white">{stats.pendingCount}</strong></span>
            </div>
            <div className="flex items-center space-x-2 text-slate-400">
              <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
              <span>Flags: <strong className="text-amber-300">{stats.flaggedCount}</strong></span>
            </div>
            <div className="flex items-center space-x-2 text-slate-400">
              <DollarSign className="w-3.5 h-3.5 text-emerald-400" />
              <span>Disbursed: <strong className="text-emerald-300">${stats.totalDisbursedUSD.toLocaleString()}</strong></span>
            </div>
          </div>
        </div>
      </div>

      {/* Dynamic Navigation Tabs */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <nav className="flex space-x-2 sm:space-x-4 overflow-x-auto py-2 text-xs font-medium">
          {activeTabsList.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-1.5 rounded transition-all whitespace-nowrap border text-xs font-semibold ${
                activeTab === tab.id
                  ? 'bg-sky-900/40 border-sky-500 text-sky-200 shadow-sm'
                  : 'bg-slate-900/50 border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
};
