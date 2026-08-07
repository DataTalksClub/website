# DataTalksClub AWS infrastructure and sandbox account

Locator: git@github.com:DataTalksClub/aws-infra.git

Accessed: 2026-08-07

## Summary

The repository and read-only AWS inspection define the development environment constraints.

## Claims

- [FACT dtc-aws-infra] The active sandbox identity is AWS account `817685572750`, with default region `eu-west-1`.
- [FACT dtc-aws-infra] Terraform is organized as independent state roots, and the shared sandbox state bucket documented by the repository is in `us-east-1`.
- [FACT dtc-aws-infra] The delegated `dtcdev.click` Route 53 zone is `Z05963572WVWFHDQZH5NE`; a second same-name zone exists and must not be selected by name-only lookup.
- [FACT dtc-aws-infra] Workload stacks own their service DNS records but must not create another `dtcdev.click` hosted zone.
- [FACT dtc-aws-infra] The sandbox currently has only the default VPC in `eu-west-1`, with public default subnets and no ECS clusters, RDS instances, or load balancers.
- [FACT dtc-aws-infra] The AI Shipping Labs production reference uses ECS Fargate, an ALB, ECR, RDS PostgreSQL, Secrets Manager, CloudWatch, SES permissions, ACM, Route 53, and GitHub OIDC deployment.

## Limitations

- [FACT dtc-aws-infra] The infrastructure repository had unrelated local changes at inspection time and must not be edited until implementation re-checks ownership and state.
- [INFERENCE dtc-aws-infra] Live AWS inventory can change and must be re-read immediately before Terraform planning.

## Related

- [HUMAN] [Human decisions](../notes/human-decisions.md)
