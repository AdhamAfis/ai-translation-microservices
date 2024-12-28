import React from 'react';
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

type HistoryItem = {
  id: string;
  timestamp: string;
  in_text: string;
  out_text: string;
  type: 'e2a-translation' | 'a2e-translation' | 'summarization';
  formality?: string;
};

type TranslationHistoryProps = {
  history: HistoryItem[];
  title: string;
};

export function TranslationHistory({ history, title }: TranslationHistoryProps) {
  return (
    <Card className="w-full">
      <CardHeader>
        <h2 className="text-xl font-semibold">{title}</h2>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[300px] w-full rounded-md border p-4">
          {history.length === 0 ? (
            <p className="text-center text-muted-foreground">No history available</p>
          ) : (
            <div className="space-y-4">
              {history.map((item) => (
                <Card key={item.id} className="p-4">
                  <div className="flex flex-col gap-2">
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-muted-foreground">
                        {new Date(item.timestamp).toLocaleString()}
                      </span>
                      {item.formality && (
                        <span className="text-sm bg-primary/10 text-primary px-2 py-1 rounded">
                          {item.formality}
                        </span>
                      )}
                    </div>
                    <div className="grid gap-2">
                      <div>
                        <p className="text-sm font-medium">Input:</p>
                        <p className="text-sm text-muted-foreground">{item.in_text}</p>
                      </div>
                      <div>
                        <p className="text-sm font-medium">Output:</p>
                        <p className="text-sm text-muted-foreground">{item.out_text}</p>
                      </div>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  );
} 