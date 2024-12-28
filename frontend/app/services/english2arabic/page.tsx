"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { callAPI } from "@/lib/api";
import React, { useState, useEffect } from "react";
import { toast } from "sonner";
import { TranslationHistory } from "@/components/history/TranslationHistory";

type TranslationResponse = {
  id: string;
  status: string;
  result: {
    translation: string;
    formality: string;
  };
  cache_hit: boolean;
};

const EnglishToArabicPage: React.FC = () => {
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    fetchHistory();
  }, []);

  const fetchHistory = async () => {
    const response = await callAPI("/history/user123?type=e2a-translation");
    if (response.data) {
      setHistory(response.data.data);
    }
  };

  const handleTranslate = async () => {
    setIsLoading(true);
    const response = await callAPI<TranslationResponse>("/e2a", {
      method: "POST",
      body: JSON.stringify({ 
        user_id: "user123",
        chat_id: Date.now().toString(),
        text: input 
      })
    });

    if (response.error) {
      toast.error(response.error.detail);
    } else if (response.data) {
      setOutput(response.data.result.translation);
      fetchHistory(); // Refresh history after new translation
    }
    
    setIsLoading(false);
  };

  return (
    <div className="container mx-auto p-4 max-w-4xl">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold">English to Arabic</h1>
      </div>
      
      <div className="grid gap-6">
        <Card>
          <CardHeader className="text-lg font-semibold">English Text</CardHeader>
          <CardContent>
            <Textarea 
              placeholder="Enter English text..."
              className="min-h-[200px]"
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
          </CardContent>
        </Card>

        <Button 
          onClick={handleTranslate} 
          disabled={!input || isLoading}
          className="w-full"
        >
          {isLoading ? "Translating..." : "Translate to Arabic"}
        </Button>

        <Card>
          <CardHeader className="text-lg font-semibold">Arabic Translation</CardHeader>
          <CardContent>
            <Textarea 
              readOnly 
              value={output}
              placeholder="الترجمة ستظهر هنا..."
              className="min-h-[150px] text-right"
              dir="rtl"
            />
          </CardContent>
        </Card>

        <TranslationHistory 
          history={history} 
          title="Translation History" 
        />
      </div>
    </div>
  );
};

export default EnglishToArabicPage;
