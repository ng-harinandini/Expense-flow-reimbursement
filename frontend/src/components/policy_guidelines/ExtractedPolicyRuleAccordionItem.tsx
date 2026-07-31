"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";

import { AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Button } from "@/components/ui/Button";
import { Separator } from "@/components/ui/separator";

import { PolicyRuleFormFields } from "./PolicyRuleFormFields";
import { policyRuleSchema, type PolicyRuleFormValues } from "./policyRuleSchema";

interface ExtractedPolicyRuleAccordionItemProps {
  itemValue: string;
  index: number;
  extractedValues: PolicyRuleFormValues;
  onSave: (values: PolicyRuleFormValues) => void;
  onDiscard: () => void;
}

export function ExtractedPolicyRuleAccordionItem({
  itemValue,
  index,
  extractedValues,
  onSave,
  onDiscard,
}: ExtractedPolicyRuleAccordionItemProps) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<PolicyRuleFormValues>({
    resolver: yupResolver(policyRuleSchema),
    defaultValues: extractedValues,
  });

  const summary = extractedValues.category
    ? `${extractedValues.category} — ${extractedValues.gradeApplicable || "Grade not set"}`
    : `Extracted rule ${index + 1}`;

  return (
    <AccordionItem value={itemValue}>
      <AccordionTrigger>
        Rule {index + 1}: {summary}
      </AccordionTrigger>
      <AccordionContent>
        <form onSubmit={handleSubmit(onSave)}>
          <PolicyRuleFormFields register={register} errors={errors} idPrefix={itemValue} />

          <Separator className="my-4" />

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onDiscard}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              Save
            </Button>
          </div>
        </form>
      </AccordionContent>
    </AccordionItem>
  );
}
