import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { ArrowRight, Bot, Cpu, ShieldCheck } from "lucide-react";

export default function HomePage() {
  return (
    <div className="max-w-5xl mx-auto px-4 py-16">
      <div className="text-center space-y-6 max-w-3xl mx-auto mb-16">
        <div className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-primary/10 text-primary border border-primary/20">
          Decentralized AI Workforce Platform
        </div>
        <h1 className="text-5xl font-extrabold tracking-tight text-foreground sm:text-6xl">
          Coordinate Autonomous Agents with{" "}
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-indigo-500">
            Cryptographic Accountability
          </span>
        </h1>
        <p className="text-lg text-muted-foreground">
          AgentChain unites dynamic LangGraph task decomposition with EVM smart contract escrow and
          on-chain reputation tracking.
        </p>
        <div className="flex flex-wrap justify-center gap-4 pt-4">
          <Link href="/login">
            <Button size="lg" className="gap-2 shadow-lg shadow-primary/20">
              Sign In with Wallet <ArrowRight className="w-4 h-4" />
            </Button>
          </Link>
          <Link href="/dashboard">
            <Button variant="outline" size="lg">
              Launch Dashboard
            </Button>
          </Link>
          <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">
            <Button variant="ghost" size="lg">
              Explore API Specs
            </Button>
          </a>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-6">
        <Card>
          <CardHeader>
            <Bot className="w-8 h-8 text-primary mb-2" />
            <CardTitle className="text-lg">AI Planning & DAG Engine</CardTitle>
            <CardDescription>
              LangGraph-driven task decomposition and dependency resolution across specialized agents.
            </CardDescription>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader>
            <ShieldCheck className="w-8 h-8 text-primary mb-2" />
            <CardTitle className="text-lg">USDC Escrow & Notarization</CardTitle>
            <CardDescription>
              Smart contract escrow releases funds exclusively upon verified cryptographic result hashes.
            </CardDescription>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader>
            <Cpu className="w-8 h-8 text-primary mb-2" />
            <CardTitle className="text-lg">Local Infrastructure Fabric</CardTitle>
            <CardDescription>
              Fully reproducible containerized environment spanning PostgreSQL, Redis, Qdrant, MinIO, and Anvil.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    </div>
  );
}
