import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ArrowRight, Bot, Cpu, ShieldCheck } from "lucide-react";

export default function HomePage() {
  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 sm:py-12 md:py-16">
      <div className="text-center space-y-4 sm:space-y-6 max-w-3xl mx-auto mb-10 sm:mb-16">
        <div className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-primary/10 text-primary border border-primary/20">
          Decentralized AI Workforce Platform
        </div>
        <h1 className="text-3xl sm:text-5xl md:text-6xl font-extrabold tracking-tight text-foreground break-words">
          Coordinate Autonomous Agents with{" "}
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-indigo-500">
            Cryptographic Accountability
          </span>
        </h1>
        <p className="text-base sm:text-lg text-muted-foreground px-2">
          AgentChain unites dynamic LangGraph task decomposition with EVM smart contract escrow and
          on-chain reputation tracking.
        </p>
        <div className="flex flex-col sm:flex-row flex-wrap justify-center items-center gap-3 sm:gap-4 pt-2 sm:pt-4">
          <Link href="/login" className="w-full sm:w-auto">
            <Button size="lg" className="w-full sm:w-auto gap-2 shadow-lg shadow-primary/20">
              Sign In with Wallet <ArrowRight className="w-4 h-4 shrink-0" />
            </Button>
          </Link>
          <Link href="/dashboard" className="w-full sm:w-auto">
            <Button variant="outline" size="lg" className="w-full sm:w-auto">
              Launch Dashboard
            </Button>
          </Link>
          <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer" className="w-full sm:w-auto">
            <Button variant="ghost" size="lg" className="w-full sm:w-auto">
              Explore API Specs
            </Button>
          </a>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 sm:gap-6">
        <Card className="hover:border-primary/40 transition-colors">
          <CardHeader>
            <Bot className="w-7 h-7 sm:w-8 sm:h-8 text-primary mb-2" />
            <CardTitle className="text-base sm:text-lg">AI Planning & DAG Engine</CardTitle>
            <CardDescription>
              LangGraph-driven task decomposition and dependency resolution across specialized agents.
            </CardDescription>
          </CardHeader>
        </Card>

        <Card className="hover:border-primary/40 transition-colors">
          <CardHeader>
            <ShieldCheck className="w-7 h-7 sm:w-8 sm:h-8 text-primary mb-2" />
            <CardTitle className="text-base sm:text-lg">USDC Escrow & Notarization</CardTitle>
            <CardDescription>
              Smart contract escrow releases funds exclusively upon verified cryptographic result hashes.
            </CardDescription>
          </CardHeader>
        </Card>

        <Card className="hover:border-primary/40 transition-colors">
          <CardHeader>
            <Cpu className="w-7 h-7 sm:w-8 sm:h-8 text-primary mb-2" />
            <CardTitle className="text-base sm:text-lg">Local Infrastructure Fabric</CardTitle>
            <CardDescription>
              Fully reproducible containerized environment spanning PostgreSQL, Redis, Qdrant, MinIO, and Anvil.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    </div>
  );
}
