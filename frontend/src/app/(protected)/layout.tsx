// import { Navbar } from "@/components/Navbar";
import { SubHeader } from "@/components/shared/SubHeader";
import { TopHeader } from "@/components/shared/TopHeader";

export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-background">
      <div className="sticky top-0 z-30 shadow-sm">
        <TopHeader
          userName="harinandinib"
          userEmail="harinandinib@example.com"
          userRole="Participant"
        />
        <SubHeader />
      </div>
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {/* <Navbar className="mb-6" /> */}
        {children}
      </div>
    </div>
  );
}
