import React, { useState } from 'react';
import { ExpenseClaim, ClaimStatus, ExpenseCategory } from '../types';
import { Search, Filter, AlertTriangle, CheckCircle2, Clock, XCircle, ShieldAlert, ArrowUpRight } from 'lucide-react';

interface ClaimsListProps {
  claims: ExpenseClaim[];
  onSelectClaim: (claim: ExpenseClaim) => void;
  onOpenSubmitModal?: () => void;
}

export const ClaimsList: React.FC<ClaimsListProps> = ({
  claims,
  onSelectClaim,
  onOpenSubmitModal
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('ALL');
  const [selectedStatus, setSelectedStatus] = useState<string>('ALL');

  const filteredClaims = claims.filter(c => {
    const matchesSearch =
      (c.claimNumber || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (c.employeeName || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (c.merchantVendor || '').toLowerCase().includes(searchQuery.toLowerCase());

    const matchesCategory = selectedCategory === 'ALL' || c.category === selectedCategory;
    const matchesStatus = selectedStatus === 'ALL' || c.status === selectedStatus;

    return matchesSearch && matchesCategory && matchesStatus;
  });

  const getStatusBadge = (status: ClaimStatus) => {
    const safeStatus = (status || '').replace('_', ' ');
    switch (status) {
      case 'Auto_Approved':
      case 'Approved':
      case 'Disbursed':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
            <CheckCircle2 className="w-3 h-3 mr-1" /> {safeStatus}
          </span>
        );
      case 'Manager_Review':
      case 'Finance_Review':
      case 'Submitted':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/10 text-amber-300 border border-amber-500/30">
            <Clock className="w-3 h-3 mr-1" /> {safeStatus}
          </span>
        );
      case 'Flagged_Fraud':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/30 font-semibold animate-pulse">
            <ShieldAlert className="w-3 h-3 mr-1" /> Flagged Fraud
          </span>
        );
      case 'Rejected':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-400 border border-slate-700">
            <XCircle className="w-3 h-3 mr-1" /> Rejected
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-300">
            {status}
          </span>
        );
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-lg p-6 text-slate-200">
      {/* Search & Filter Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
        <div>
          <h2 className="text-lg font-bold text-white">Expense Claims Console</h2>
          <p className="text-xs text-slate-400">Review claims, AI policy violations, and fraud risk scores</p>
        </div>

        {onOpenSubmitModal && (
          <button
            onClick={onOpenSubmitModal}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg text-xs transition-all shadow-md self-start md:self-auto"
          >
            + Submit New Expense
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
        {/* Search */}
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
          <input
            type="text"
            placeholder="Search by claim #, vendor, employee..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        {/* Category Filter */}
        <select
          value={selectedCategory}
          onChange={(e) => setSelectedCategory(e.target.value)}
          className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        >
          <option value="ALL">All Categories</option>
          <option value="Meals">Meals</option>
          <option value="Ground Transport">Ground Transport</option>
          <option value="Flights">Flights</option>
          <option value="Lodging">Lodging</option>
          <option value="Client Entertainment">Client Entertainment</option>
          <option value="Software & Subscriptions">Software & Subscriptions</option>
        </select>

        {/* Status Filter */}
        <select
          value={selectedStatus}
          onChange={(e) => setSelectedStatus(e.target.value)}
          className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        >
          <option value="ALL">All Statuses</option>
          <option value="Auto_Approved">Auto Approved</option>
          <option value="Manager_Review">Manager Review</option>
          <option value="Finance_Review">Finance Review</option>
          <option value="Flagged_Fraud">Flagged Fraud</option>
          <option value="Approved">Approved</option>
          <option value="Disbursed">Disbursed</option>
        </select>
      </div>

      {/* Claims Table */}
      <div className="overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-xs text-slate-300">
          <thead className="bg-slate-950 text-slate-400 font-semibold uppercase tracking-wider border-b border-slate-800">
            <tr>
              <th className="p-3">Claim Ref</th>
              <th className="p-3">Employee</th>
              <th className="p-3">Vendor / Category</th>
              <th className="p-3">Amount</th>
              <th className="p-3">Date</th>
              <th className="p-3">Risk Score</th>
              <th className="p-3">Status</th>
              <th className="p-3 text-right">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 bg-slate-900/40">
            {filteredClaims.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-slate-500">
                  No claims found matching your filter criteria.
                </td>
              </tr>
            ) : (
              filteredClaims.map((claim) => (
                <tr
                  key={claim.id}
                  onClick={() => onSelectClaim(claim)}
                  className="hover:bg-slate-800/50 transition-colors cursor-pointer group"
                >
                  <td className="p-3 font-mono font-semibold text-indigo-300">
                    {claim.claimNumber}
                  </td>
                  <td className="p-3">
                    <div className="font-medium text-white">{claim.employeeName}</div>
                    <div className="text-[11px] text-slate-500">Grade {claim.employeeGrade} • {claim.department}</div>
                  </td>
                  <td className="p-3">
                    <div className="font-medium text-slate-200">{claim.merchantVendor}</div>
                    <div className="text-[11px] text-slate-400">{claim.category}</div>
                  </td>
                  <td className="p-3 font-semibold text-slate-100">
                    ${claim.amountUSD.toFixed(2)} USD
                  </td>
                  <td className="p-3 text-slate-400 whitespace-nowrap">
                    {claim.expenseDate}
                  </td>
                  <td className="p-3">
                    {claim.fraudScreening ? (
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${
                        claim.fraudScreening.riskScore >= 70
                          ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                          : claim.fraudScreening.riskScore >= 20
                          ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                          : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                      }`}>
                        {claim.fraudScreening.riskScore}/100 ({claim.fraudScreening.riskLevel})
                      </span>
                    ) : (
                      <span className="text-slate-500">N/A</span>
                    )}
                  </td>
                  <td className="p-3">
                    {getStatusBadge(claim.status)}
                  </td>
                  <td className="p-3 text-right">
                    <span className="inline-flex items-center text-indigo-400 group-hover:text-indigo-300 font-medium text-xs">
                      Inspect <ArrowUpRight className="w-3.5 h-3.5 ml-1" />
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
